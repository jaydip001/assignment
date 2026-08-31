# Scenario 2 — Different models and changing limits

*Generated: 2026-08-28 10:16:19 — all numbers are MEASURED on this machine (environment in BENCHMARK_REPORT.md).*

## Pass criteria

- ✅ PASS — **each model stays within its current limits (referee: zero 429s)**: 429s — model-a: 0, model-b: 0
- ✅ PASS — **model A within reduced limit (last 60s of constrained phase)**: A dispatched 4,817 in that window (limit 5,000)
- ✅ PASS — **model A speeds back up after the restore**: A dispatched 29,000 in the final minute (restored limit 30,000)
- ✅ PASS — **no restart (same service process the whole time)**: service pid 6411 unchanged; limit changes were live API calls
- ✅ PASS — **model B keeps processing while A is constrained**: B dispatched 19,272 in the last minute of A's constrained phase (B's ceiling 20,000)
- ✅ PASS — **every submitted request accounted for**: submitted 379,486 = completed 240,290 + rejected 0 + expired 0 + waiting 139,196

**Overall: ✅ ALL CRITERIA PASS**

## Setup

A: 30,000 RPM (cut to 5,000 at t=90s, restored at t=210s, live via PUT /admin/models/model-a/limits). B: 20,000 RPM untouched. Load: ~700/s on A, ~450/s on B.

## Configured limit changes (gateway audit log)

```json
[
  {
    "at": 1787892107.683649,
    "model": "model-a",
    "rpm": 5000.0,
    "tpm": 60000000.0
  },
  {
    "at": 1787892229.190614,
    "model": "model-a",
    "rpm": 30000.0,
    "tpm": 60000000.0
  }
]
```

## Model A throughput over time (per 10s)

| t (s) | requests dispatched | tokens |
|---|---|---|
| 0–10 | 5529 | 5,529,000 |
| 10–20 | 4900 | 4,900,000 |
| 20–30 | 4901 | 4,901,000 |
| 30–40 | 4901 | 4,901,000 |
| 40–50 | 4901 | 4,901,000 |
| 50–60 | 4268 | 4,268,000 |
| 60–70 | 5131 | 5,131,000 |
| 70–80 | 4899 | 4,899,000 |
| 80–90 | 4899 | 4,899,000 |
| 90–100 | 353 | 353,000 |
| 100–110 | 0 | 0 |
| 110–120 | 0 | 0 |
| 120–130 | 0 | 0 |
| 130–140 | 0 | 0 |
| 140–150 | 882 | 882,000 |
| 150–160 | 817 | 817,000 |
| 160–170 | 817 | 817,000 |
| 170–180 | 816 | 816,000 |
| 180–190 | 817 | 817,000 |
| 190–200 | 751 | 751,000 |
| 200–210 | 799 | 799,000 |
| 210–220 | 4009 | 4,009,000 |
| 220–230 | 4898 | 4,898,000 |
| 230–240 | 4900 | 4,900,000 |
| 240–250 | 4901 | 4,901,000 |
| 250–260 | 4900 | 4,900,000 |
| 260–270 | 4900 | 4,900,000 |
| 270–280 | 4501 | 4,501,000 |
| 280–290 | 4902 | 4,902,000 |
| 290–300 | 4899 | 4,899,000 |
| 300–310 | 4900 | 4,900,000 |
| 310–320 | 4901 | 4,901,000 |
| 320–330 | 4897 | 4,897,000 |
| 330–340 | 4412 | 4,412,000 |
| 340–350 | 4897 | 4,897,000 |
| 350–360 | 4900 | 4,900,000 |
| 360–370 | 1473 | 1,473,000 |

## Model B throughput over time (per 10s)

| t (s) | requests dispatched | tokens |
|---|---|---|
| 0–10 | 3686 | 3,686,000 |
| 10–20 | 3267 | 3,267,000 |
| 20–30 | 3267 | 3,267,000 |
| 30–40 | 3268 | 3,268,000 |
| 40–50 | 3267 | 3,267,000 |
| 50–60 | 2845 | 2,845,000 |
| 60–70 | 3421 | 3,421,000 |
| 70–80 | 3266 | 3,266,000 |
| 80–90 | 3267 | 3,267,000 |
| 90–100 | 3268 | 3,268,000 |
| 100–110 | 3264 | 3,264,000 |
| 110–120 | 3114 | 3,114,000 |
| 120–130 | 3095 | 3,095,000 |
| 130–140 | 3267 | 3,267,000 |
| 140–150 | 3264 | 3,264,000 |
| 150–160 | 3267 | 3,267,000 |
| 160–170 | 3268 | 3,268,000 |
| 170–180 | 3265 | 3,265,000 |
| 180–190 | 2940 | 2,940,000 |
| 190–200 | 3269 | 3,269,000 |
| 200–210 | 3265 | 3,265,000 |
| 210–220 | 3268 | 3,268,000 |
| 220–230 | 3265 | 3,265,000 |
| 230–240 | 3266 | 3,266,000 |
| 240–250 | 2942 | 2,942,000 |
| 250–260 | 3268 | 3,268,000 |
| 260–270 | 3265 | 3,265,000 |
| 270–280 | 3265 | 3,265,000 |
| 280–290 | 3267 | 3,267,000 |
| 290–300 | 3268 | 3,268,000 |
| 300–310 | 2832 | 2,832,000 |
| 310–320 | 3376 | 3,376,000 |
| 320–330 | 3267 | 3,267,000 |
| 330–340 | 3265 | 3,265,000 |
| 340–350 | 3268 | 3,268,000 |
| 350–360 | 3264 | 3,264,000 |
| 360–370 | 929 | 929,000 |

## Load generator accounting

```json
{
  "succeeded": 240290,
  "failed": 0,
  "rejected": 0,
  "expired": 0,
  "waiting": 139196,
  "unknown": 0,
  "latency_ms": {
    "end_to_end": {
      "p50": 64211.5,
      "p95": 178073.7,
      "p99": 183897.8
    },
    "provider_only": {
      "p50": 133.3,
      "p95": 211.2,
      "p99": 342.8
    }
  }
}
```

## Referee (simulator) verdict

```json
{
  "model-a": {
    "config": {
      "rpm": 30000,
      "tpm": 60000000,
      "latency_ms": [
        60,
        180
      ],
      "failure_rate": 0.0,
      "transient_rate": 0.0
    },
    "stats": {
      "accepted": 123571,
      "rejected_429": 0,
      "succeeded": 123571,
      "failed": 0,
      "transient_503": 0
    },
    "worst_60s_window_observed": {
      "max_rpm_observed": 29400,
      "max_tpm_observed": 29400000
    }
  },
  "model-b": {
    "config": {
      "rpm": 20000,
      "tpm": 40000000,
      "latency_ms": [
        60,
        180
      ],
      "failure_rate": 0.0,
      "transient_rate": 0.0
    },
    "stats": {
      "accepted": 117375,
      "rejected_429": 0,
      "succeeded": 117332,
      "failed": 0,
      "transient_503": 0
    },
    "worst_60s_window_observed": {
      "max_rpm_observed": 19600,
      "max_tpm_observed": 19600000
    }
  }
}
```
