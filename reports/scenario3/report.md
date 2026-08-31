# Scenario 3 — Asynchronous batch completion

*Generated: 2026-08-31 23:14:38 — all numbers are MEASURED on this machine (environment in BENCHMARK_REPORT.md).*

## Pass criteria

- ✅ PASS — **initial acknowledgement within one second**: ack for 10,000 requests took 37ms
- ✅ PASS — **callback sent only after all requests reached a final state**: batch completed at t+4.63s; first callback attempt at t+4.63s; callback shows pending=0
- ✅ PASS — **delivery succeeds after the destination recovers**: attempt responses: [503, 503, 200]
- ✅ PASS — **callback summary matches the batch status from the API**: callback: total=10000 succeeded=9699 failed=301 | API: total=10000 succeeded=9699 failed=301
- ✅ PASS — **every request id appears exactly once in the final batch result**: 10,000 results, 10,000 unique, matches submitted ids
- ✅ PASS — **simulated permanent failures occurred and were tracked**: 301 requests ended in a final 'failed' state
- ✅ PASS — **simulated transient failures occurred and were retried**: 510 requests needed more than one provider attempt

**Overall: ✅ ALL CRITERIA PASS**

## Setup

One batch of 10,000 requests across two models with a callback_url. Simulator: 5% transient 503s (retried with fresh permits) + 3% permanent failures. Receiver rejects its first 2 deliveries.

## Timing

```json
{
  "ack_ms": 36.8,
  "batch_completed_after_s": 4.63,
  "callback_attempts": [
    {
      "n": 1,
      "at_s": 4.63,
      "response": 503
    },
    {
      "n": 2,
      "at_s": 5.14,
      "response": 503
    },
    {
      "n": 3,
      "at_s": 6.14,
      "response": 200
    }
  ]
}
```

## Callback payload (as delivered)

```json
{
  "batch_id": "batch-8fa3922d4dfa",
  "status": "completed",
  "total": 10000,
  "pending": 0,
  "succeeded": 9699,
  "failed": 301,
  "failed_breakdown": {
    "provider_failed": 301,
    "rejected": 0,
    "expired": 0
  },
  "created_at": 1788198272.265122,
  "completed_at": 1788198276.870259,
  "callback": {
    "attempts": 3,
    "delivered_at": null,
    "last_error": "destination returned 503"
  },
  "results_url": "http://127.0.0.1:8103/v1/batches/batch-8fa3922d4dfa/results"
}
```

## Batch status from the API

```json
{
  "batch_id": "batch-8fa3922d4dfa",
  "status": "completed",
  "total": 10000,
  "pending": 0,
  "succeeded": 9699,
  "failed": 301,
  "failed_breakdown": {
    "provider_failed": 301,
    "rejected": 0,
    "expired": 0
  },
  "created_at": 1788198272.265122,
  "completed_at": 1788198276.870259,
  "callback": {
    "attempts": 3,
    "delivered_at": 1788198278.381661,
    "last_error": null
  }
}
```
