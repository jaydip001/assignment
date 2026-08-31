# Benchmark report

Sections 1–5 are **measured** on the machine below by the scripts in this
repo. Section 6 is a **projection** and labelled as such. Raw outputs live in
`reports/`.

## 1. Test environment

| | |
|---|---|
| Machine | Apple M4 Pro, 14 CPU cores, 24 GB RAM |
| OS | macOS 26.4 |
| Runtime | Python 3.14.5 (deps pinned in `requirements.txt`) |
| Network | loopback only (gateway, simulator, loadgen on one host) |

Reproduce any number with `make scenario1|scenario2|scenario3|bench|bench-stretch`.

## 2. Simulation assumptions

* The **provider is simulated**: per-request latency uniform 80–250 ms in
  scenario 1 (60–180 ms in scenario 2, 30–90 ms in scenario 3), plus
  configurable transient-503 and permanent-failure rates. It independently
  enforces RPM/TPM with its own limiter and returns 429 on violation, so
  compliance is verified by a second party.
* Scenarios 1–3 use **real HTTP** end to end.
* The 300k/1M benchmarks replace HTTP with an **in-process** provider model
  (assumptions at the top of `bench.py`); the rate limiting on that path is
  the production code. A request counts as completed only when its simulated
  call reaches a final success/failure state.
* `estimated_tokens` is trusted as the token cost (as with real providers
  pre-response).

## 3. Provider-capacity test — 50,000 RPM / 100M TPM (Scenario 1, measured)

Five minutes of 1000-token single requests at ~1,000/s, deliberately ~20%
above the 833/s ceiling. Full report: `reports/scenario1/report.md`.

| metric | value | pass bar |
|---|---|---|
| Utilisation after 60s warm-up | **96.5%** | ≥ 90% |
| Worst rolling 60s window (gateway ledger) | **49,000 / 50,000 req** | ≤ limit |
| Worst rolling 60s window (referee, independent) | **49,000 req / 49.0M tokens** | ≤ limit |
| Provider 429s | **0** | 0 |
| Accounting | exact: submitted = succeeded + rejected + expired + waiting | exact |
| Latency p50/p95/p99 (provider-only) | ~174 / 252 / 316 ms | — |
| Latency p50/p95/p99 (end-to-end incl. queue wait) | ~31 / 38 / 39 s | — |

End-to-end latency is dominated by queue wait **by design**: the run holds
sustained 20% overload, so the bounded queue (30,000) fills, adds ~37s of
wait at the ceiling rate, and the overflow is rejected with 429 — the
defined overload behaviour, all visible in the accounting. The ~3.5%
utilisation gap is deliberate: a 2% safety factor for clock skew plus the
limiter's extra-second window conservatism.

Scenario 2 (live limit changes) and scenario 3 (async batch + flaky
callback) evidence: `reports/scenario2/report.md`, `reports/scenario3/report.md`.

## 4. Required and stretch benchmarks (measured)

30-second runs, 10 shard processes, simulated latency 20–30 ms, 1% failure
rate, model limits 20% above target (the brief permits higher simulated
limits). `make bench` / `make bench-stretch`.

| benchmark | target | steady-state achieved | total completed | latency p50/p95/p99 | accounting |
|---|---|---|---|---|---|
| Required | 300,000/s | **~300,000/s** | ~9.0M | 25.0 / 30.2 / 30.8 ms | exact |
| Stretch | 1,000,000/s | **~1,000,000/s** | ~30.0M | 25.0 / 30.1 / 30.7 ms | exact |

"Completed" = the simulated provider call reached success/failure; steady
state = mean completions/second excluding 2s warm-up and the drain tail. In
every run: submitted == completed + waiting-at-end.

**Limiter-under-pressure run** (`reports/bench_limited.json`): demand 300k/s
against a 12M RPM (= 200k/s) model. Completions cap at ~211k/s over the 20s
run (the per-minute window allows a short run to front-load), latency shows
honest queueing (p50 ~2.9s), and the bounded queue rejects ~100k requests.
Demand above capacity breaks neither compliance nor accounting. Reproduce:
`python bench.py --target-rps 300000 --duration 20 --rpm-limit 12000000`.

## 5. Bottlenecks observed

1. **HTTP path**: one uvicorn gateway process handles ~1,000 submissions/s
   plus ~800 dispatches/s comfortably; several thousand/s would saturate the
   API process first. Fix: more API workers, or batch submission (one POST
   carries 10,000 requests — acked in ~30 ms in scenario 3).
2. **Benchmark path**: one Python process tops out around ~150k
   completions/s; sharding (each process owning 1/N of the budget) is what
   reaches 300k–1M/s, with headroom left on this machine.
3. **Memory**: the in-memory store keeps every request record for the run
   (~75 MB per 250k requests). Fine here; a retention window or external
   store is the production fix.

## 6. Projection — toward 10B requests/minute (NOT measured)

10B/min ≈ 167M req/s. The design extends by leasing slices of each model's
global budget to many nodes (the same sharding `bench.py` demonstrates), a
partitioned log for the queue, and a KV store for request state. Full plan
in the README's scaling section. No number there is a measurement.
