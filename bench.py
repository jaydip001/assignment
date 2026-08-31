from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import random
import time
from collections import defaultdict, deque

from service import RateLimiter

TICK = 0.005


def run_shard(shard_id, a, conn):
    rng = random.Random(1000 + shard_id)
    shard_rps = a["target_rps"] / a["processes"]
    limiter = RateLimiter(a["rpm_limit"] / a["processes"], a["tpm_limit"] / a["processes"])
    lat_lo, lat_hi = a["latency_ms"] * 0.8 / 1000, a["latency_ms"] * 1.2 / 1000
    max_queue = int(shard_rps * 5)
    epoch_offset = (time.time() - a["epoch"]) - time.monotonic()

    pending = deque()
    wheel = defaultdict(list)
    latencies = []
    RESERVOIR = 100_000
    per_sec = defaultdict(int)
    submitted = rejected = completed = succeeded = failed = seen = 0

    start = time.monotonic()
    end_of_arrivals = start + a["duration"]
    drain_deadline = end_of_arrivals + 10
    next_tick = start
    last_tick = int(start / TICK)

    while True:
        now = time.monotonic()

        if now < end_of_arrivals:
            for _ in range(int(shard_rps * (now - start)) - submitted - rejected):
                if len(pending) >= max_queue:
                    rejected += 1
                else:
                    pending.append(now)
                    submitted += 1

        grant = limiter.take_batch(len(pending), a["tokens"])
        for _ in range(grant):
            ts = pending.popleft()
            wheel[int((now + rng.uniform(lat_lo, lat_hi)) / TICK)].append(ts)

        current_tick = int(now / TICK)
        for tick in range(last_tick + 1, current_tick + 1):
            for ts in wheel.pop(tick, ()):
                completed += 1
                if rng.random() < a["failure_rate"]:
                    failed += 1
                else:
                    succeeded += 1
                per_sec[int(now + epoch_offset)] += 1
                seen += 1
                if len(latencies) < RESERVOIR:
                    latencies.append(now - ts)
                elif rng.random() < RESERVOIR / seen:
                    latencies[rng.randrange(RESERVOIR)] = now - ts
        last_tick = current_tick

        if (now >= end_of_arrivals and not pending and not wheel) or now >= drain_deadline:
            break
        next_tick += TICK
        sleep = next_tick - time.monotonic()
        if sleep > 0:
            time.sleep(sleep)
        else:
            next_tick = time.monotonic()

    conn.send({"submitted": submitted, "rejected": rejected, "completed": completed,
               "succeeded": succeeded, "failed": failed,
               "still_waiting": len(pending) + sum(len(v) for v in wheel.values()),
               "latencies": latencies, "per_sec": dict(per_sec)})
    conn.close()


def percentile(values, p):
    return values[min(len(values) - 1, int(round(p / 100 * (len(values) - 1))))] if values else None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--target-rps", type=int, default=300_000)
    p.add_argument("--duration", type=float, default=30.0)
    p.add_argument("--processes", type=int, default=max(2, mp.cpu_count() - 4))
    p.add_argument("--tokens", type=int, default=1000)
    p.add_argument("--latency-ms", type=float, default=25.0)
    p.add_argument("--failure-rate", type=float, default=0.01)
    p.add_argument("--rpm-limit", type=float, default=None)
    p.add_argument("--tpm-limit", type=float, default=None)
    p.add_argument("--out", default=None)
    args = p.parse_args()
    if args.rpm_limit is None:
        args.rpm_limit = args.target_rps * 60 * 1.2
    if args.tpm_limit is None:
        args.tpm_limit = args.rpm_limit * args.tokens

    a = vars(args)
    a["epoch"] = time.time()
    print(f"benchmark: target {args.target_rps:,} req/s for {args.duration}s "
          f"across {args.processes} shards")
    print(f"model limits: {args.rpm_limit:,.0f} RPM / {args.tpm_limit:,.0f} TPM")

    wall_start = time.monotonic()
    procs, pipes = [], []
    for shard in range(args.processes):
        parent, child = mp.Pipe(duplex=False)
        proc = mp.Process(target=run_shard, args=(shard, a, child))
        proc.start()
        procs.append(proc)
        pipes.append(parent)
    results = [conn.recv() for conn in pipes]
    for proc in procs:
        proc.join()
    wall = time.monotonic() - wall_start

    totals = {k: sum(r[k] for r in results) for k in
              ("submitted", "rejected", "completed", "succeeded", "failed", "still_waiting")}
    per_sec = defaultdict(int)
    for r in results:
        for sec, n in r["per_sec"].items():
            per_sec[int(sec)] += n
    steady = [per_sec[s] for s in sorted(per_sec) if 2 <= s < args.duration] or [0]
    lat = sorted(v for r in results for v in r["latencies"])

    report = {
        "mode": "SIMULATION (in-process provider)",
        "config": {k: v for k, v in a.items() if k != "out"},
        "results_measured": {
            **totals,
            "wall_seconds": round(wall, 1),
            "achieved_rps_steady_state": round(sum(steady) / len(steady)),
            "achieved_rps_peak_second": max(steady),
            "target_met": sum(steady) / len(steady) >= args.target_rps * 0.95,
            "latency_ms": {p: round(percentile(lat, v) * 1000, 2) if lat else None
                           for p, v in (("p50", 50), ("p95", 95), ("p99", 99))},
            "accounting_check": totals["submitted"]
            == totals["completed"] + totals["still_waiting"],
        },
        "completions_per_second": {str(k): per_sec[k] for k in sorted(per_sec)},
    }

    r = report["results_measured"]
    print("\n================ BENCHMARK REPORT (measured) ================")
    print(f"submitted:   {totals['submitted']:,}   rejected: {totals['rejected']:,}")
    print(f"completed:   {totals['completed']:,}   "
          f"(succeeded {totals['succeeded']:,} / failed {totals['failed']:,})")
    print(f"steady-state rate: {r['achieved_rps_steady_state']:,}/s   "
          f"peak second: {r['achieved_rps_peak_second']:,}/s   target: {args.target_rps:,}/s")
    print(f"latency ms p50/p95/p99: {r['latency_ms']['p50']} / "
          f"{r['latency_ms']['p95']} / {r['latency_ms']['p99']}")
    print(f"accounting exact (submitted == completed + waiting): {r['accounting_check']}")
    print(f"TARGET {'MET' if r['target_met'] else 'NOT MET'}")
    if args.out:
        with open(args.out, "w") as f:
            json.dump(report, f, indent=2)
        print(f"full report written to {args.out}")


if __name__ == "__main__":
    main()
