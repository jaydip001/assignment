# Scenario 3 — Asynchronous batch completion

*Generated: 2026-09-05 13:04:01 — all numbers are MEASURED on this machine (environment in BENCHMARK_REPORT.md).*

## Pass criteria

- ✅ PASS — **initial acknowledgement within one second**: ack for 10,000 requests took 40ms
- ✅ PASS — **callback sent only after all requests reached a final state**: batch completed at t+4.20s; first callback attempt at t+4.20s; callback shows pending=0
- ✅ PASS — **delivery succeeds after the destination recovers**: attempt responses: [503, 503, 200]
- ✅ PASS — **callback summary matches the batch status from the API**: callback: total=10000 succeeded=9691 failed=309 | API: total=10000 succeeded=9691 failed=309
- ✅ PASS — **every request id appears exactly once in the final batch result**: 10,000 results, 10,000 unique, matches submitted ids
- ✅ PASS — **simulated permanent failures occurred and were tracked**: 309 requests ended in a final 'failed' state
- ✅ PASS — **simulated transient failures occurred and were retried**: 519 requests needed more than one provider attempt

**Overall: ✅ ALL CRITERIA PASS**

## Setup

One batch of 10,000 requests across two models with a callback_url. Simulator: 5% transient 503s (retried with fresh permits) + 3% permanent failures. Receiver rejects its first 2 deliveries.

## Timing

```json
{
  "ack_ms": 40.0,
  "batch_completed_after_s": 4.2,
  "callback_attempts": [
    {
      "n": 1,
      "at_s": 4.2,
      "response": 503
    },
    {
      "n": 2,
      "at_s": 4.71,
      "response": 503
    },
    {
      "n": 3,
      "at_s": 5.71,
      "response": 200
    }
  ]
}
```

## Callback payload (as delivered)

```json
{
  "batch_id": "batch-cbfda3e2ba5b",
  "status": "completed",
  "total": 10000,
  "pending": 0,
  "succeeded": 9691,
  "failed": 309,
  "failed_breakdown": {
    "provider_failed": 309,
    "rejected": 0,
    "expired": 0
  },
  "created_at": 1788593635.599851,
  "completed_at": 1788593639.77471,
  "callback": {
    "attempts": 3,
    "delivered_at": null,
    "last_error": "destination returned 503"
  },
  "results_url": "http://127.0.0.1:8103/v1/batches/batch-cbfda3e2ba5b/results"
}
```

## Batch status from the API

```json
{
  "batch_id": "batch-cbfda3e2ba5b",
  "status": "completed",
  "total": 10000,
  "pending": 0,
  "succeeded": 9691,
  "failed": 309,
  "failed_breakdown": {
    "provider_failed": 309,
    "rejected": 0,
    "expired": 0
  },
  "created_at": 1788593635.599851,
  "completed_at": 1788593639.77471,
  "callback": {
    "attempts": 3,
    "delivered_at": 1788593641.2853062,
    "last_error": null
  }
}
```
