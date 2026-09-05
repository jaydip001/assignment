# inference-at-scale

A system that processes inference requests as fast as a provider's RPM/TPM
limits allow — and never faster — plus a provider simulator, a load
generator, scripted validation scenarios, and a benchmark that completes
300k–1M simulated requests/second.

```
 clients                     GATEWAY (service.py :8000)              SIMULATOR (simulator.py :9000)
 POST /v1/requests   ──►  per-model queue ─► rate limiter ─► HTTP ──►  enforces its own limits
 POST /v1/batches           (bounded)      (bucket + 60s     ──►      (429 if we ever exceed),
   ack < 1s, callback                       sliding window)           simulates latency + failures
 GET  status/results        PUT /admin/models/{m}/limits = live limit changes, no restart
```

## Run it

Needs Python 3.11+. No Docker, no external services.

```bash
make install         # venv + pinned deps

make run-simulator   # terminal 1: fake provider (port 9000)
make run-service     # terminal 2: gateway       (port 8000)
make loadgen-demo    # terminal 3: 200 req/s for 30s + report
```

Validation scenarios (self-contained — they start their own services and
write PASS/FAIL evidence to `reports/scenarioN/report.md`):

```bash
make scenario1       # ~7 min: fill 50k RPM / 100M TPM to >90%, never exceed
make scenario2       # ~7 min: cut model A to 5k RPM live, restore; B untouched
make scenario3       # ~1 min: 10k batch, flaky callback destination
make bench           # 300,000 completed requests/second (simulation)
make bench-stretch   # 1,000,000 completed requests/second (simulation)
```

Add `--quick` for shorter smoke runs: `.venv/bin/python scenarios.py 1 --quick`.

## API

```bash
# single request
curl -X POST localhost:8000/v1/requests -H 'content-type: application/json' \
  -d '{"model": "model-a", "estimated_tokens": 1000, "payload": {"prompt": "hi"}}'

curl localhost:8000/v1/requests/<id>            # status + result + latency

# batch with completion callback
curl -X POST localhost:8000/v1/batches -H 'content-type: application/json' \
  -d '{"requests": [{"model": "model-a", "estimated_tokens": 500}],
       "callback_url": "http://myapp/callback"}'

curl localhost:8000/v1/batches/<id>             # poll independently any time
curl localhost:8000/v1/batches/<id>/results     # every request, exactly once
```

When the last request of a batch reaches a final state, the gateway POSTs the
summary (status, total/succeeded/failed, `results_url`) to the callback URL,
retrying with exponential backoff (up to 10 attempts) if it's down.

## Streaming

Single requests can be streamed token-by-token over SSE:

```bash
curl -N -X POST localhost:8000/v1/requests/stream \
  -H 'content-type: application/json' \
  -d '{"model": "model-a", "estimated_tokens": 500, "payload": {"prompt": "hi"}}'
```

The response is a live event stream: a metadata event with the `request_id`,
then one event per generated chunk, then a final event with the terminal
status and the time-to-first-token (`ttft_ms`).

Design rules for the streaming path:

* **Rate limiting is unchanged** — a stream is one request; it takes one
  RPM/TPM permit at dispatch, exactly like a normal request.
* **Completion = end of stream** — the request reaches its final state when
  the last chunk arrives (or the stream dies), so accounting stays exact.
* **Retries only before the first chunk** — a transient 503/timeout before
  any output is retried with a fresh permit; once the client has seen data,
  an error surfaces as `failed` ("stream interrupted") instead of silently
  restarting the answer.
* **Idle timeout, not total** — a stream fails if no chunk arrives for 20s
  (`STREAM_IDLE_SECONDS`), so long healthy generations are never killed.
* **Client disconnects are final states** — hanging up mid-stream aborts the
  provider call and marks the request `failed` ("client disconnected").
* Streaming is per-request only; batch items cannot set `stream` (batches
  are fire-and-forget with callbacks by design).

The simulator streams when asked (`stream_chunks` per model, default 8,
spread across the configured latency) and can fail mid-stream.

## Configuring models and changing limits

Startup config is `config.json` (shared by gateway and simulator — the
gateway reads `rpm`/`tpm`, the simulator also reads `latency_ms`,
`failure_rate`, `transient_rate`).

At runtime, with no restart:

```bash
# tell the gateway the provider cut our quota
curl -X PUT localhost:8000/admin/models/model-a/limits \
  -H 'content-type: application/json' -d '{"rpm": 5000}'

# make the simulated provider enforce the new quota
curl -X PUT 127.0.0.1:9000/admin/models/model-a \
  -H 'content-type: application/json' -d '{"rpm": 5000}'
```

Because the limiter keeps a ledger of the last 60 seconds, a reduction takes
effect immediately: if recent traffic already exceeds the new budget,
dispatching pauses until the window drains.

## Load generator

```bash
.venv/bin/python loadgen.py \
  --rate 1200 --duration 300 \                # request rate + duration
  --model model-a=0.6 --model model-b=0.4 \   # model mix
  --batch-size 100 \                          # 1 = single requests
  --tokens 1000 --tokens-jitter 50 \          # approximate token size
  --out reports/myrun.json
```

The end-of-run report covers: submitted / completed / succeeded / failed /
rejected / expired / waiting, achieved completion rate, p50/p95/p99 latency
(end-to-end and provider-only), configured vs observed RPM and TPM per model
(worst rolling 60s window from both the gateway's ledger and the simulator's
independent one), and batch + callback timing.

## Design decisions and tradeoffs

**Token bucket + sliding window.** The bucket (refill = limit/60 per second,
~2s burst) paces traffic smoothly; the 60-second ledger is the hard
guarantee that no rolling minute exceeds RPM or TPM. The ledger keeps one
extra second-bucket, so it covers slightly more than any true 60s span — it
can only over-protect. The gateway dispatches at 98% of the limit
(`SAFETY = 0.98`) to absorb clock skew against the provider; measured
utilisation is ~96% against a 90% pass bar.

**The simulator is a referee, not a mock.** It enforces limits with the same
limiter class and returns 429 on violation. Every report shows both ledgers,
and the simulator's 429 count (zero in all runs) is the independent proof.

**Defined overload behaviour.** Bounded per-model queue; when full, requests
are rejected immediately with 429. Optional per-request `ttl_seconds` expires
requests that wait too long. Every request ends in exactly one of
succeeded / failed / rejected / expired, or is still visibly waiting — the
scenario reports check that the accounting adds up exactly.

**Retries consume permits.** Transient provider errors (503/timeout) are
retried with backoff, and each retry takes a fresh RPM/TPM permit — a retry
is a real provider call. Requests that can never fit (oversized tokens,
limits cut below their size) fail immediately instead of blocking the queue.

**Callbacks are at-least-once.** Exactly-once over HTTP doesn't exist;
receivers should treat `batch_id` as an idempotency key. Batches stay
queryable regardless, so a dead webhook never loses data.

**Single process, in-memory state.** One asyncio event loop means no locks
and code you can review in an afternoon. The costs (state lost on restart,
one machine's throughput) are the first things the scaling plan below fixes.

## Scaling to 10 billion requests/minute (projection, not measured)

10B/min ≈ 167M req/s — a horizontal problem. Each piece here has a direct
horizontal analogue:

1. **Shard the budget, not the bucket.** A small capacity-allocator service
   leases slices of each model's global budget to gateway nodes
   (`Σ leases ≤ limit`); each node enforces its slice with this repo's
   in-process limiter — zero cross-node coordination on the hot path.
   `bench.py` demonstrates exactly this: N processes × limit/N. Limit changes
   propagate on lease renewal (seconds); if the allocator is down, nodes
   decay their last lease — fail safe, never over-send.
2. **The queue becomes a partitioned log** (Kafka), request state moves to a
   sharded KV store, batch counters become sharded counters whose last
   decrement fires the callback service.
3. **Fleet sizing** (order of magnitude, from measured single-node numbers
   and assuming a compiled dispatcher at ~50k dispatch/s per node): ~2,000
   edge/API nodes, ~3,500 dispatchers, a 3–5 node allocator control plane,
   ~100 callback nodes. Per-request work is O(1) and coordination-free, so
   throughput scales linearly with dispatchers until provider capacity
   itself is the wall.
4. **What changes qualitatively:** client request ids become mandatory
   (dedup under at-least-once delivery), admission control moves to the edge
   (per-tenant quotas), and the per-second ledgers become Prometheus
   histograms with a compliance alert.

## Measured vs simulated

Everything in `reports/` and `BENCHMARK_REPORT.md` is measured on this
machine; the scenarios use real HTTP end to end. The provider itself is
simulated (latency, outcomes — configured in `config.json`). The 300k/1M
benchmarks replace HTTP with an in-process provider model (assumptions at the
top of `bench.py`); the rate limiting on that path is the production code. A
request only counts as completed when its provider call reaches a final
success/failure state — never at API acceptance.

## Files

| file | what it is |
|---|---|
| `service.py` | the gateway: rate limiter, per-model queues, batches, callbacks, API |
| `simulator.py` | the provider: enforces limits (429), simulates latency + failures |
| `loadgen.py` | open-loop load generator + end-of-run report |
| `bench.py` | 300k–1M req/s sharded benchmark |
| `scenarios.py` | scenarios 1–3, self-contained, write PASS/FAIL evidence |
| `config.json` | model limits + simulator behaviour |
| `BENCHMARK_REPORT.md` | measured results on the test machine |
| `reports/` | generated evidence (scenario reports, benchmark JSON) |
