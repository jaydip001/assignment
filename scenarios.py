from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from argparse import Namespace
from pathlib import Path

import aiohttp
from aiohttp import web

from loadgen import LoadGenerator

ROOT = Path(__file__).resolve().parent
REPORTS = ROOT / "reports"


class Proc:
    def __init__(self, name, app, port, config_path, extra_env=None):
        log_dir = REPORTS / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        self.name = name
        self.log = open(log_dir / f"{name}.log", "w")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", app, "--port", str(port),
             "--host", "127.0.0.1", "--log-level", "warning"],
            cwd=ROOT, env={**os.environ, "CONFIG": config_path, **(extra_env or {})},
            stdout=self.log, stderr=subprocess.STDOUT)

    async def wait_healthy(self, url, timeout=30):
        deadline = time.monotonic() + timeout
        async with aiohttp.ClientSession() as s:
            while time.monotonic() < deadline:
                if self.proc.poll() is not None:
                    raise RuntimeError(f"{self.name} exited early")
                try:
                    async with s.get(url) as resp:
                        if resp.status == 200:
                            return
                except aiohttp.ClientError:
                    pass
                await asyncio.sleep(0.25)
        raise TimeoutError(f"{self.name} not healthy after {timeout}s")

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.log.close()


async def start_stack(name, sim_port, svc_port, models):
    cfg_dir = REPORTS / "logs"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg = str(cfg_dir / f"{name}-config.json")
    Path(cfg).write_text(json.dumps({"models": models}, indent=2))
    sim = Proc(f"{name}-simulator", "simulator:app", sim_port, cfg)
    svc = Proc(f"{name}-service", "service:app", svc_port, cfg,
               {"PROVIDER_URL": f"http://127.0.0.1:{sim_port}",
                "PUBLIC_BASE_URL": f"http://127.0.0.1:{svc_port}"})
    await sim.wait_healthy(f"http://127.0.0.1:{sim_port}/health")
    await svc.wait_healthy(f"http://127.0.0.1:{svc_port}/health")
    return sim, svc


async def http_json(method, url, body=None):
    async with aiohttp.ClientSession() as s:
        async with s.request(method, url, json=body) as resp:
            resp.raise_for_status()
            return await resp.json()


class Checks:
    def __init__(self):
        self.items = []

    def check(self, name, ok, detail):
        self.items.append({"name": name, "pass": bool(ok), "detail": detail})
        print(f"  [{'PASS' if ok else 'FAIL'}] {name} — {detail}")

    @property
    def all_pass(self):
        return all(i["pass"] for i in self.items)


def write_report(name, title, checks, sections):
    out = REPORTS / name
    out.mkdir(parents=True, exist_ok=True)
    lines = [f"# {title}", "",
             f"*Generated: {time.strftime('%Y-%m-%d %H:%M:%S')} — all numbers are MEASURED "
             f"on this machine (environment in BENCHMARK_REPORT.md).*", "", "## Pass criteria", ""]
    for i in checks.items:
        lines.append(f"- {'✅ PASS' if i['pass'] else '❌ FAIL'} — **{i['name']}**: {i['detail']}")
    lines += ["", f"**Overall: {'✅ ALL CRITERIA PASS' if checks.all_pass else '❌ FAILED'}**", ""]
    for heading, content in sections.items():
        lines += [f"## {heading}", ""]
        lines += [content, ""] if isinstance(content, str) else \
                 ["```json", json.dumps(content, indent=2), "```", ""]
    (out / "report.md").write_text("\n".join(lines))
    (out / "report.json").write_text(json.dumps(
        {"title": title, "checks": checks.items, "all_pass": checks.all_pass,
         "sections": sections}, indent=2))
    print(f"\nreport written to {out / 'report.md'}")


def per_10s_table(series):
    if not series:
        return "(no data)"
    rows = ["| t (s) | requests dispatched | tokens |", "|---|---|---|"]
    t0 = series[0]["second"]
    for i in range(0, series[-1]["second"] - t0 + 1, 10):
        bucket = [p for p in series if i <= p["second"] - t0 < i + 10]
        rows.append(f"| {i}–{i+10} | {sum(p['requests'] for p in bucket)} "
                    f"| {sum(p['tokens'] for p in bucket):,} |")
    return "\n".join(rows)


def loadgen_args(svc_port, sim_port, **kw):
    defaults = dict(service_url=f"http://127.0.0.1:{svc_port}",
                    simulator_url=f"http://127.0.0.1:{sim_port}",
                    rate=1000, duration=60, model=["model-a=1.0"], tokens=1000,
                    tokens_jitter=0, batch_size=1, callback_url=None, ttl=None,
                    submit_concurrency=256, drain_timeout=120, out=None)
    return Namespace(**{**defaults, **kw})


async def scenario1(quick):
    RPM, TPM = 50_000, 100_000_000
    duration = 90 if quick else 300
    sim, svc = await start_stack("scenario1", 9101, 8101, {
        "model-a": {"rpm": RPM, "tpm": TPM, "latency_ms": [80, 250],
                    "queue_size": 30_000}})
    try:
        result = await LoadGenerator(loadgen_args(
            8101, 9101, rate=1000, duration=duration)).run()

        completion = await http_json("GET",
            "http://127.0.0.1:8101/metrics/timeline?model=model-a&kind=completion")
        dispatch = await http_json("GET",
            "http://127.0.0.1:8101/metrics/timeline?model=model-a&kind=dispatch")
        referee = (await http_json("GET", "http://127.0.0.1:9101/admin/stats"))["model-a"]

        c = Checks()
        acc = result["accounting"]
        sub = result["submission"]

        c.check("load was submitted above the configured capacity",
                sub["actual_submit_rate"] > RPM / 60
                and (acc["rejected"] > 0 or acc["waiting"] > 0),
                f"submitted at {sub['actual_submit_rate']}/s vs capacity {RPM/60:.0f}/s; "
                f"overload visible as rejected={acc['rejected']:,}, waiting={acc['waiting']:,}")

        series = completion["series"]
        t0 = series[0]["second"] if series else 0
        after = [p for p in series if p["second"] - t0 >= 60]
        done = sum(p["requests"] for p in after)
        allowed = RPM * len(after) / 60
        c.check("completes >= 90% of allowed capacity after warm-up",
                allowed and done / allowed >= 0.90,
                f"{done:,} completed in {len(after)}s after warm-up; allowed {allowed:,.0f}; "
                f"utilisation {done / allowed:.1%}")

        gw = dispatch["worst_60s_window"]
        ref = referee["worst_60s_window_observed"]
        c.check("no 60s window exceeds RPM (gateway ledger)",
                gw["max_rpm_observed"] <= RPM,
                f"worst rolling 60s = {gw['max_rpm_observed']:,} of {RPM:,}")
        c.check("no 60s window exceeds TPM (gateway ledger)",
                gw["max_tpm_observed"] <= TPM,
                f"worst rolling 60s = {gw['max_tpm_observed']:,} of {TPM:,}")
        c.check("no 60s window exceeds limits (referee, independent)",
                ref["max_rpm_observed"] <= RPM and ref["max_tpm_observed"] <= TPM,
                f"referee observed worst 60s: {ref['max_rpm_observed']:,} req / "
                f"{ref['max_tpm_observed']:,} tokens")
        c.check("provider never returned 429",
                referee["stats"]["rejected_429"] == 0,
                f"referee 429 count = {referee['stats']['rejected_429']}")

        accounted = (acc["succeeded"] + acc["failed"] + acc["rejected"] + acc["expired"]
                     + acc["waiting"] + sub["submit_errors"])
        c.check("every submitted request accounted for",
                accounted == sub["submitted"] and acc["unknown"] == 0,
                f"submitted {sub['submitted']:,} = succeeded {acc['succeeded']:,} "
                f"+ failed {acc['failed']:,} + rejected {acc['rejected']:,} "
                f"+ expired {acc['expired']:,} + waiting {acc['waiting']:,}")

        write_report("scenario1", "Scenario 1 — Reach the available provider capacity", c, {
            "Setup": f"model-a at {RPM:,} RPM / {TPM:,} TPM; 1000-token requests at 1000/s "
                     f"for {duration}s (above the ~833/s ceiling). Overload behaviour: "
                     f"bounded queue (30,000) -> excess rejected with 429.",
            "Load generator accounting": acc,
            "Achieved completion rate": result["achieved_completion_rate"],
            "Latency (ms, measured server-side)": acc["latency_ms"],
            "Gateway dispatch per 10s": per_10s_table(dispatch["series"]),
            "Referee (simulator) verdict": referee,
        })
        return c.all_pass
    finally:
        svc.stop()
        sim.stop()


async def scenario2(quick):
    A_RPM, B_RPM = 30_000, 20_000
    A_REDUCED = 5_000
    p1, p2, p3 = (30, 90, 60) if quick else (90, 120, 120)
    duration = p1 + p2 + p3
    sim, svc = await start_stack("scenario2", 9102, 8102, {
        "model-a": {"rpm": A_RPM, "tpm": 60_000_000, "latency_ms": [60, 180]},
        "model-b": {"rpm": B_RPM, "tpm": 40_000_000, "latency_ms": [60, 180]}})
    try:
        pid_before = (await http_json("GET", "http://127.0.0.1:8102/health"))["pid"]

        async def set_rpm(rpm, reducing):
            gateway = ("PUT", "http://127.0.0.1:8102/admin/models/model-a/limits", {"rpm": rpm})
            referee = ("PUT", "http://127.0.0.1:9102/admin/models/model-a", {"rpm": rpm})
            for call in (gateway, referee) if reducing else (referee, gateway):
                await http_json(*call)
                await asyncio.sleep(0.5)
            print(f"  >> limit change applied: model-a rpm={rpm}")

        async def limit_changer():
            await asyncio.sleep(p1)
            await set_rpm(A_REDUCED, reducing=True)
            await asyncio.sleep(p2)
            await set_rpm(A_RPM, reducing=False)

        t_start = time.time()
        result, _ = await asyncio.gather(
            LoadGenerator(loadgen_args(8102, 9102, rate=1150, duration=duration,
                                       model=["model-a=0.61", "model-b=0.39"],
                                       drain_timeout=30)).run(),
            limit_changer())

        a_disp = await http_json("GET",
            "http://127.0.0.1:8102/metrics/timeline?model=model-a&kind=dispatch")
        b_disp = await http_json("GET",
            "http://127.0.0.1:8102/metrics/timeline?model=model-b&kind=dispatch")
        referee = await http_json("GET", "http://127.0.0.1:9102/admin/stats")
        gw = await http_json("GET", "http://127.0.0.1:8102/metrics")
        pid_after = (await http_json("GET", "http://127.0.0.1:8102/health"))["pid"]

        def window(series, lo, hi):
            return sum(p["requests"] for p in series
                       if t_start + lo <= p["second"] < t_start + hi)

        c = Checks()
        c.check("each model stays within its current limits (referee: zero 429s)",
                referee["model-a"]["stats"]["rejected_429"] == 0
                and referee["model-b"]["stats"]["rejected_429"] == 0,
                f"429s — model-a: {referee['model-a']['stats']['rejected_429']}, "
                f"model-b: {referee['model-b']['stats']['rejected_429']}")

        a_low = window(a_disp["series"], p1 + p2 - 60, p1 + p2)
        c.check("model A within reduced limit (last 60s of constrained phase)",
                0 < a_low <= A_REDUCED,
                f"A dispatched {a_low:,} in that window (limit {A_REDUCED:,})")
        a_back = window(a_disp["series"], duration - 60, duration)
        c.check("model A speeds back up after the restore",
                a_back >= 0.8 * A_RPM * 0.98,
                f"A dispatched {a_back:,} in the final minute (restored limit {A_RPM:,})")
        c.check("no restart (same service process the whole time)",
                pid_before == pid_after,
                f"service pid {pid_before} unchanged; limit changes were live API calls")
        b_during = window(b_disp["series"], p1 + p2 - 60, p1 + p2)
        c.check("model B keeps processing while A is constrained",
                b_during >= 0.85 * B_RPM * 0.98,
                f"B dispatched {b_during:,} in the last minute of A's constrained phase "
                f"(B's ceiling {B_RPM:,})")

        acc = result["accounting"]
        sub = result["submission"]
        accounted = (acc["succeeded"] + acc["failed"] + acc["rejected"] + acc["expired"]
                     + acc["waiting"] + sub["submit_errors"])
        c.check("every submitted request accounted for",
                accounted == sub["submitted"] and acc["unknown"] == 0,
                f"submitted {sub['submitted']:,} = completed "
                f"{acc['succeeded'] + acc['failed']:,} + rejected {acc['rejected']:,} "
                f"+ expired {acc['expired']:,} + waiting {acc['waiting']:,}")

        write_report("scenario2", "Scenario 2 — Different models and changing limits", c, {
            "Setup": f"A: {A_RPM:,} RPM (cut to {A_REDUCED:,} at t={p1}s, restored at "
                     f"t={p1+p2}s, live via PUT /admin/models/model-a/limits). "
                     f"B: {B_RPM:,} RPM untouched. Load: ~700/s on A, ~450/s on B.",
            "Configured limit changes (gateway audit log)": gw["limit_change_log"],
            "Model A throughput over time (per 10s)": per_10s_table(a_disp["series"]),
            "Model B throughput over time (per 10s)": per_10s_table(b_disp["series"]),
            "Load generator accounting": acc,
            "Referee (simulator) verdict": referee,
        })
        return c.all_pass
    finally:
        svc.stop()
        sim.stop()


class FlakyReceiver:
    def __init__(self):
        self.attempts = []
        self.payload = None
        self.delivered = asyncio.Event()

    async def handle(self, request):
        n = len(self.attempts) + 1
        self.attempts.append({"n": n, "at": time.time(), "response": 503 if n <= 2 else 200})
        if n <= 2:
            return web.json_response({"error": "receiver down"}, status=503)
        self.payload = await request.json()
        self.delivered.set()
        return web.json_response({"ok": True})


async def scenario3(_quick):
    N = 10_000
    sim, svc = await start_stack("scenario3", 9103, 8103, {
        m: {"rpm": 60_000, "tpm": 100_000_000, "latency_ms": [30, 90],
            "transient_rate": 0.05, "failure_rate": 0.03}
        for m in ("model-a", "model-b")})
    receiver = FlakyReceiver()
    web_app = web.Application()
    web_app.router.add_post("/callback", receiver.handle)
    runner = web.AppRunner(web_app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 8203).start()
    try:
        items = [{"request_id": f"s3-req-{i:05d}",
                  "model": "model-a" if i % 2 == 0 else "model-b",
                  "estimated_tokens": 500, "payload": {"prompt": f"item {i}"}}
                 for i in range(N)]

        t0 = time.time()
        ack = await http_json("POST", "http://127.0.0.1:8103/v1/batches",
                              {"requests": items,
                               "callback_url": "http://127.0.0.1:8203/callback"})
        ack_s = time.time() - t0
        print(f"  batch {ack['batch_id']} acknowledged in {ack_s*1000:.0f}ms")

        await asyncio.wait_for(receiver.delivered.wait(), timeout=300)
        cb = receiver.payload
        api = await http_json("GET", f"http://127.0.0.1:8103/v1/batches/{ack['batch_id']}")
        results = (await http_json(
            "GET", f"http://127.0.0.1:8103/v1/batches/{ack['batch_id']}/results"))["results"]

        c = Checks()
        c.check("initial acknowledgement within one second", ack_s < 1.0,
                f"ack for {N:,} requests took {ack_s*1000:.0f}ms")
        c.check("callback sent only after all requests reached a final state",
                api["completed_at"] is not None
                and receiver.attempts[0]["at"] >= api["completed_at"]
                and cb["pending"] == 0 and cb["status"] == "completed",
                f"batch completed at t+{api['completed_at']-t0:.2f}s; first callback attempt "
                f"at t+{receiver.attempts[0]['at']-t0:.2f}s; callback shows pending=0")
        responses = [a["response"] for a in receiver.attempts]
        c.check("delivery succeeds after the destination recovers",
                responses[:2] == [503, 503] and responses[-1] == 200,
                f"attempt responses: {responses}")
        c.check("callback summary matches the batch status from the API",
                all(cb[k] == api[k] for k in
                    ("batch_id", "status", "total", "succeeded", "failed")),
                f"callback: total={cb['total']} succeeded={cb['succeeded']} "
                f"failed={cb['failed']} | API: total={api['total']} "
                f"succeeded={api['succeeded']} failed={api['failed']}")
        ids = [r["request_id"] for r in results]
        c.check("every request id appears exactly once in the final batch result",
                sorted(ids) == sorted(i["request_id"] for i in items),
                f"{len(ids):,} results, {len(set(ids)):,} unique, matches submitted ids")
        failed = sum(1 for r in results if r["status"] == "failed")
        retried = sum(1 for r in results if r["attempts"] > 1)
        c.check("simulated permanent failures occurred and were tracked", failed > 0,
                f"{failed:,} requests ended in a final 'failed' state")
        c.check("simulated transient failures occurred and were retried", retried > 0,
                f"{retried:,} requests needed more than one provider attempt")

        write_report("scenario3", "Scenario 3 — Asynchronous batch completion", c, {
            "Setup": f"One batch of {N:,} requests across two models with a callback_url. "
                     f"Simulator: 5% transient 503s (retried with fresh permits) + 3% "
                     f"permanent failures. Receiver rejects its first 2 deliveries.",
            "Timing": {"ack_ms": round(ack_s * 1000, 1),
                       "batch_completed_after_s": round(api["completed_at"] - t0, 2),
                       "callback_attempts": [
                           {"n": a["n"], "at_s": round(a["at"] - t0, 2),
                            "response": a["response"]} for a in receiver.attempts]},
            "Callback payload (as delivered)": cb,
            "Batch status from the API": api,
        })
        return c.all_pass
    finally:
        await runner.cleanup()
        svc.stop()
        sim.stop()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("scenario", choices=["1", "2", "3"])
    p.add_argument("--quick", action="store_true", help="shorter smoke run")
    args = p.parse_args()
    ok = asyncio.run({"1": scenario1, "2": scenario2, "3": scenario3}[args.scenario](args.quick))
    raise SystemExit(0 if ok else 1)
