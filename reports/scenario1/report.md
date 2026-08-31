# Scenario 1 — Reach the available provider capacity

*Generated: 2026-08-28 10:15:56 — all numbers are MEASURED on this machine (environment in BENCHMARK_REPORT.md).*

## Pass criteria

- ✅ PASS — **load was submitted above the configured capacity**: submitted at 999.9/s vs capacity 833/s; overload visible as rejected=26,630, waiting=0
- ✅ PASS — **completes >= 90% of allowed capacity after warm-up**: 224,350 completed in 279s after warm-up; allowed 232,500; utilisation 96.5%
- ✅ PASS — **no 60s window exceeds RPM (gateway ledger)**: worst rolling 60s = 49,000 of 50,000
- ✅ PASS — **no 60s window exceeds TPM (gateway ledger)**: worst rolling 60s = 49,000,000 of 100,000,000
- ✅ PASS — **no 60s window exceeds limits (referee, independent)**: referee observed worst 60s: 49,000 req / 49,000,000 tokens
- ✅ PASS — **provider never returned 429**: referee 429 count = 0
- ✅ PASS — **every submitted request accounted for**: submitted 299,980 = succeeded 273,350 + failed 0 + rejected 26,630 + expired 0 + waiting 0

**Overall: ✅ ALL CRITERIA PASS**

## Setup

model-a at 50,000 RPM / 100,000,000 TPM; 1000-token requests at 1000/s for 300s (above the ~833/s ceiling). Overload behaviour: bounded queue (30,000) -> excess rejected with 429.

## Load generator accounting

```json
{
  "succeeded": 273350,
  "failed": 0,
  "rejected": 26630,
  "expired": 0,
  "waiting": 0,
  "unknown": 0,
  "latency_ms": {
    "end_to_end": {
      "p50": 30705.1,
      "p95": 38004.9,
      "p99": 39254.6
    },
    "provider_only": {
      "p50": 176.7,
      "p95": 263.5,
      "p99": 350.3
    }
  }
}
```

## Achieved completion rate

```json
{
  "completed_per_second": 808.3,
  "run_seconds_including_drain": 338.2,
  "completed_of_submitted_pct": 91.12
}
```

## Latency (ms, measured server-side)

```json
{
  "end_to_end": {
    "p50": 30705.1,
    "p95": 38004.9,
    "p99": 39254.6
  },
  "provider_only": {
    "p50": 176.7,
    "p95": 263.5,
    "p99": 350.3
  }
}
```

## Gateway dispatch per 10s

| t (s) | requests dispatched | tokens |
|---|---|---|
| 0–10 | 9484 | 9,484,000 |
| 10–20 | 8165 | 8,165,000 |
| 20–30 | 8166 | 8,166,000 |
| 30–40 | 8169 | 8,169,000 |
| 40–50 | 8167 | 8,167,000 |
| 50–60 | 6849 | 6,849,000 |
| 60–70 | 8820 | 8,820,000 |
| 70–80 | 8163 | 8,163,000 |
| 80–90 | 8173 | 8,173,000 |
| 90–100 | 8161 | 8,161,000 |
| 100–110 | 8170 | 8,170,000 |
| 110–120 | 7513 | 7,513,000 |
| 120–130 | 7825 | 7,825,000 |
| 130–140 | 8344 | 8,344,000 |
| 140–150 | 8167 | 8,167,000 |
| 150–160 | 8165 | 8,165,000 |
| 160–170 | 8167 | 8,167,000 |
| 170–180 | 8166 | 8,166,000 |
| 180–190 | 6990 | 6,990,000 |
| 190–200 | 8530 | 8,530,000 |
| 200–210 | 8163 | 8,163,000 |
| 210–220 | 8169 | 8,169,000 |
| 220–230 | 8169 | 8,169,000 |
| 230–240 | 8165 | 8,165,000 |
| 240–250 | 6803 | 6,803,000 |
| 250–260 | 8710 | 8,710,000 |
| 260–270 | 8172 | 8,172,000 |
| 270–280 | 8162 | 8,162,000 |
| 280–290 | 8168 | 8,168,000 |
| 290–300 | 8167 | 8,167,000 |
| 300–310 | 6622 | 6,622,000 |
| 310–320 | 8894 | 8,894,000 |
| 320–330 | 8167 | 8,167,000 |
| 330–340 | 6465 | 6,465,000 |

## Referee (simulator) verdict

```json
{
  "config": {
    "rpm": 50000,
    "tpm": 100000000,
    "latency_ms": [
      80,
      250
    ],
    "failure_rate": 0.0,
    "transient_rate": 0.0,
    "queue_size": 30000
  },
  "stats": {
    "accepted": 273350,
    "rejected_429": 0,
    "succeeded": 273350,
    "failed": 0,
    "transient_503": 0
  },
  "worst_60s_window_observed": {
    "max_rpm_observed": 49000,
    "max_tpm_observed": 49000000
  }
}
```
