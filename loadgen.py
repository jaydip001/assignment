from __future__ import annotations

import argparse
import asyncio
import json
import random
import time

import aiohttp


def percentile(values, p):
    if not values:
        return None
    return values[min(len(values) - 1, int(round(p / 100 * (len(values) - 1))))]


class LoadGenerator:
    def __init__(self, args):
        self.args = args
        self.mix = [(m.partition("=")[0], float(m.partition("=")[2] or 1.0))
                    for m in args.model]
        self.request_ids = []
        self.batch_ids = []
        self.submit_errors = 0
        self.session = None

    def _item(self, n):
        tokens = self.args.tokens
        if self.args.tokens_jitter:
            tokens = max(1, int(random.gauss(tokens, self.args.tokens_jitter)))
        item = {"model": random.choices([m for m, _ in self.mix],
                                        weights=[w for _, w in self.mix])[0],
                "estimated_tokens": tokens, "payload": {"prompt": f"load-test {n}"}}
        if self.args.ttl:
            item["ttl_seconds"] = self.args.ttl
        return item

    async def _submit_single(self, item):
        try:
            async with self.session.post(f"{self.args.service_url}/v1/requests",
                                         json=item) as resp:
                self.request_ids.append((await resp.json())["request_id"])
        except Exception:
            self.submit_errors += 1

    async def _submit_batch(self, items):
        try:
            async with self.session.post(
                    f"{self.args.service_url}/v1/batches",
                    json={"requests": items, "callback_url": self.args.callback_url}) as resp:
                self.batch_ids.append((await resp.json())["batch_id"])
        except Exception:
            self.submit_errors += len(items)

    async def submit(self):
        sem = asyncio.Semaphore(self.args.submit_concurrency)
        in_flight = set()
        submitted = 0
        pending_batch = []
        start = time.monotonic()

        async def guarded(coro):
            async with sem:
                await coro

        def spawn(coro):
            task = asyncio.create_task(guarded(coro))
            in_flight.add(task)
            task.add_done_callback(in_flight.discard)

        while time.monotonic() - start < self.args.duration:
            due = int(self.args.rate * (time.monotonic() - start)) - submitted
            for _ in range(due):
                item = self._item(submitted)
                submitted += 1
                if self.args.batch_size > 1:
                    pending_batch.append(item)
                    if len(pending_batch) >= self.args.batch_size:
                        spawn(self._submit_batch(pending_batch))
                        pending_batch = []
                else:
                    spawn(self._submit_single(item))
            await asyncio.sleep(0.02)

        if pending_batch:
            spawn(self._submit_batch(pending_batch))
        if in_flight:
            await asyncio.gather(*in_flight, return_exceptions=True)

        elapsed = time.monotonic() - start
        return {"submitted": submitted, "submit_seconds": round(elapsed, 1),
                "actual_submit_rate": round(submitted / elapsed, 1),
                "submit_errors": self.submit_errors}

    async def wait_for_drain(self):
        deadline = time.monotonic() + self.args.drain_timeout
        while time.monotonic() < deadline:
            async with self.session.get(f"{self.args.service_url}/metrics") as resp:
                if (await resp.json())["waiting_total"] == 0:
                    return
            await asyncio.sleep(1)

    async def account(self):
        if self.batch_ids:
            for bid in self.batch_ids:
                async with self.session.get(
                        f"{self.args.service_url}/v1/batches/{bid}/results") as resp:
                    body = await resp.json()
                self.request_ids.extend(r["request_id"] for r in body["results"])

        counts = {"succeeded": 0, "failed": 0, "rejected": 0, "expired": 0,
                  "waiting": 0, "unknown": 0}
        e2e, prov = [], []
        for i in range(0, len(self.request_ids), 2000):
            chunk = self.request_ids[i:i + 2000]
            async with self.session.post(f"{self.args.service_url}/v1/requests/bulk_status",
                                         json={"ids": chunk}) as resp:
                statuses = await resp.json()
            for rid in chunk:
                st = statuses.get(rid)
                if st is None:
                    counts["unknown"] += 1
                elif st["status"] in ("queued", "running"):
                    counts["waiting"] += 1
                else:
                    counts[st["status"]] += 1
                    if st["status"] in ("succeeded", "failed"):
                        if st["latency_ms"] is not None:
                            e2e.append(st["latency_ms"])
                        if st["provider_latency_ms"] is not None:
                            prov.append(st["provider_latency_ms"])
        e2e.sort()
        prov.sort()
        counts["latency_ms"] = {
            "end_to_end": {p: percentile(e2e, v) for p, v in
                           (("p50", 50), ("p95", 95), ("p99", 99))},
            "provider_only": {p: percentile(prov, v) for p, v in
                              (("p50", 50), ("p95", 95), ("p99", 99))},
        }
        return counts

    async def batch_report(self):
        out = []
        for bid in self.batch_ids:
            async with self.session.get(f"{self.args.service_url}/v1/batches/{bid}") as resp:
                s = await resp.json()
            out.append({"batch_id": bid, "status": s["status"], "total": s["total"],
                        "succeeded": s["succeeded"], "failed": s["failed"],
                        "batch_duration_s": round(s["completed_at"] - s["created_at"], 2)
                        if s["completed_at"] else None,
                        "callback": s["callback"]})
        return out

    async def limit_evidence(self):
        async with self.session.get(f"{self.args.service_url}/metrics") as resp:
            gw = await resp.json()
        evidence = {"gateway": {}, "referee_simulator": {}}
        for model, _ in self.mix:
            m = gw["models"].get(model, {})
            evidence["gateway"][model] = {k: m.get(k) for k in
                                          ("configured_rpm", "configured_tpm", "worst_60s_window")}
        if self.args.simulator_url:
            try:
                async with self.session.get(f"{self.args.simulator_url}/admin/stats") as resp:
                    sim = await resp.json()
                for model, _ in self.mix:
                    s = sim.get(model, {})
                    evidence["referee_simulator"][model] = {
                        "configured_rpm": s.get("config", {}).get("rpm"),
                        "configured_tpm": s.get("config", {}).get("tpm"),
                        "rejected_429": s.get("stats", {}).get("rejected_429"),
                        "worst_60s_window_observed": s.get("worst_60s_window_observed")}
            except Exception as exc:
                evidence["referee_simulator"] = {"error": str(exc)}
        return evidence

    async def run(self):
        async with aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(limit=0),
                timeout=aiohttp.ClientTimeout(total=60)) as session:
            self.session = session
            print(f"submitting ~{self.args.rate}/s for {self.args.duration}s "
                  f"(models: {dict(self.mix)}, batch_size: {self.args.batch_size}, "
                  f"tokens: {self.args.tokens}) ...")
            run_started = time.monotonic()
            submission = await self.submit()
            print(f"submission done: {submission}")
            print("waiting for the system to drain ...")
            await self.wait_for_drain()
            run_seconds = time.monotonic() - run_started
            print("collecting final state of every request ...")
            accounting = await self.account()
            batches = await self.batch_report() if self.batch_ids else []
            evidence = await self.limit_evidence()

        completed = accounting["succeeded"] + accounting["failed"]
        return {
            "config": vars(self.args),
            "submission": submission,
            "accounting": accounting,
            "completed": completed,
            "achieved_completion_rate": {
                "completed_per_second": round(completed / run_seconds, 1),
                "run_seconds_including_drain": round(run_seconds, 1),
                "completed_of_submitted_pct": round(
                    100 * completed / max(1, submission["submitted"]), 2)},
            "batches": batches,
            "limit_evidence": evidence,
        }


def main():
    p = argparse.ArgumentParser(description="load generator for the inference gateway")
    p.add_argument("--service-url", default="http://127.0.0.1:8000")
    p.add_argument("--simulator-url", default="http://127.0.0.1:9000")
    p.add_argument("--rate", type=float, default=1000, help="requests per second to submit")
    p.add_argument("--duration", type=float, default=60, help="seconds to keep submitting")
    p.add_argument("--model", action="append",
                   help="model mix, e.g. --model model-a=0.6 --model model-b=0.4")
    p.add_argument("--tokens", type=int, default=1000)
    p.add_argument("--tokens-jitter", type=int, default=0)
    p.add_argument("--batch-size", type=int, default=1, help=">1 submits via /v1/batches")
    p.add_argument("--callback-url", default=None)
    p.add_argument("--ttl", type=float, default=None, help="per-request TTL seconds")
    p.add_argument("--submit-concurrency", type=int, default=256)
    p.add_argument("--drain-timeout", type=float, default=180)
    p.add_argument("--out", default=None, help="write full JSON report here")
    args = p.parse_args()
    args.model = args.model or ["model-a=1.0"]

    report = asyncio.run(LoadGenerator(args).run())

    acc = report["accounting"]
    rate = report["achieved_completion_rate"]
    print("\n================ LOAD TEST REPORT ================")
    print(f"submitted:  {report['submission']['submitted']} "
          f"(actual rate {report['submission']['actual_submit_rate']}/s)")
    print(f"completed:  {report['completed']}  "
          f"(succeeded {acc['succeeded']}, failed {acc['failed']})")
    print(f"achieved completion rate: {rate['completed_per_second']}/s over "
          f"{rate['run_seconds_including_drain']}s "
          f"({rate['completed_of_submitted_pct']}% of submitted)")
    print(f"rejected: {acc['rejected']}  expired: {acc['expired']}  "
          f"waiting: {acc['waiting']}  unknown: {acc['unknown']}  "
          f"submit_errors: {report['submission']['submit_errors']}")
    print(f"latency e2e ms:      {acc['latency_ms']['end_to_end']}")
    print(f"latency provider ms: {acc['latency_ms']['provider_only']}")
    print(json.dumps(report["limit_evidence"], indent=2))
    if report["batches"]:
        done = sum(1 for b in report["batches"] if b["status"] == "completed")
        print(f"batches: {len(report['batches'])} submitted, {done} completed")
    if args.out:
        with open(args.out, "w") as f:
            json.dump(report, f, indent=2)
        print(f"full report written to {args.out}")


if __name__ == "__main__":
    main()
