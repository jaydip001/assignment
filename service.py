from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager

import aiohttp
from fastapi import FastAPI, HTTPException, Request as HttpRequest
from fastapi.responses import JSONResponse

PROVIDER_URL = os.environ.get("PROVIDER_URL", "http://127.0.0.1:9000")
CONFIG = os.environ.get("CONFIG", "config.json")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8000")

QUEUE_SIZE = 200_000
MAX_CONCURRENCY = 1024
MAX_RETRIES = 3
SAFETY = 0.98
POLL = 0.01
CB_ATTEMPTS = 10


class RateLimiter:
    def __init__(self, rpm, tpm, safety=1.0, burst_seconds=2.0):
        self.safety, self.burst = safety, burst_seconds
        self.rpm, self.tpm = float(rpm), float(tpm)
        self.permits_r, self.permits_t = self._caps()
        self.last_refill = time.monotonic()
        self.buckets = deque()
        self.win_reqs = self.win_toks = 0

    def _caps(self):
        return (max(1.0, self.rpm * self.safety / 60 * self.burst),
                max(1.0, self.tpm * self.safety / 60 * self.burst))

    def update(self, rpm=None, tpm=None):
        if rpm is not None:
            self.rpm = float(rpm)
        if tpm is not None:
            self.tpm = float(tpm)
        cap_r, cap_t = self._caps()
        self.permits_r = min(self.permits_r, cap_r)
        self.permits_t = min(self.permits_t, cap_t)

    def _tick(self, now):
        cap_r, cap_t = self._caps()
        dt = now - self.last_refill
        self.last_refill = now
        self.permits_r = min(cap_r, self.permits_r + dt * self.rpm * self.safety / 60)
        self.permits_t = min(cap_t, self.permits_t + dt * self.tpm * self.safety / 60)
        cutoff = int(now) - 61
        while self.buckets and self.buckets[0][0] <= cutoff:
            _, r, t = self.buckets.popleft()
            self.win_reqs -= r
            self.win_toks -= t

    def _record(self, n, toks, now):
        sec = int(now)
        if self.buckets and self.buckets[-1][0] == sec:
            self.buckets[-1][1] += n
            self.buckets[-1][2] += toks
        else:
            self.buckets.append([sec, n, toks])
        self.win_reqs += n
        self.win_toks += toks

    def try_acquire(self, tokens):
        now = time.monotonic()
        self._tick(now)
        if (self.win_reqs + 1 > self.rpm * self.safety
                or self.win_toks + tokens > self.tpm * self.safety
                or self.permits_r < 1 or self.permits_t < tokens):
            return False
        self.permits_r -= 1
        self.permits_t -= tokens
        self._record(1, tokens, now)
        return True

    def take_batch(self, n, tokens_each):
        now = time.monotonic()
        self._tick(now)
        grant = min(n, int(self.rpm * self.safety) - self.win_reqs, int(self.permits_r))
        if tokens_each:
            grant = min(grant, (int(self.tpm * self.safety) - self.win_toks) // tokens_each,
                        int(self.permits_t) // tokens_each)
        if grant <= 0:
            return 0
        self.permits_r -= grant
        self.permits_t -= grant * tokens_each
        self._record(grant, grant * tokens_each, now)
        return grant

    def can_ever_grant(self, tokens):
        return (self.rpm * self.safety >= 1
                and tokens <= min(self.tpm * self.safety, self._caps()[1]))

    def max_tokens(self):
        return int(min(self.tpm * self.safety, self._caps()[1]))

    def snapshot(self):
        self._tick(time.monotonic())
        return {"configured_rpm": self.rpm, "configured_tpm": self.tpm,
                "requests_in_last_60s": self.win_reqs, "tokens_in_last_60s": self.win_toks}


class Timeline:
    def __init__(self):
        self.requests = defaultdict(int)
        self.tokens = defaultdict(int)

    def record(self, tokens):
        sec = int(time.time())
        self.requests[sec] += 1
        self.tokens[sec] += tokens

    def series(self):
        if not self.requests:
            return []
        lo, hi = min(self.requests), max(self.requests)
        return [{"second": s, "requests": self.requests.get(s, 0),
                 "tokens": self.tokens.get(s, 0)} for s in range(lo, hi + 1)]

    def worst_60s_window(self):
        series = self.series()
        max_r = max_t = win_r = win_t = 0
        for i, p in enumerate(series):
            win_r += p["requests"]
            win_t += p["tokens"]
            if i >= 60:
                win_r -= series[i - 60]["requests"]
                win_t -= series[i - 60]["tokens"]
            max_r, max_t = max(max_r, win_r), max(max_t, win_t)
        return {"max_rpm_observed": max_r, "max_tpm_observed": max_t}


TERMINAL = {"succeeded", "failed", "rejected", "expired"}

requests: dict[str, dict] = {}
batches: dict[str, dict] = {}
status_counts = defaultdict(int)
counters = defaultdict(int)
dispatch_tl = defaultdict(Timeline)
completion_tl = defaultdict(Timeline)
limit_change_log: list[dict] = []
pipelines: dict[str, dict] = {}
background_tasks: set[asyncio.Task] = set()
http: aiohttp.ClientSession | None = None
started_at = time.time()


def spawn(coro):
    task = asyncio.get_running_loop().create_task(coro)
    background_tasks.add(task)
    task.add_done_callback(background_tasks.discard)


def finish(req, status, error=None, result=None):
    status_counts[req["status"]] -= 1
    status_counts[status] += 1
    req["status"] = status
    req["error"] = error if error is not None else req["error"]
    req["result"] = result if result is not None else req["result"]
    req["completed"] = time.time()
    counters[status] += 1
    batch = batches.get(req["batch"])
    if batch:
        batch["pending"] -= 1
        batch[status] += 1
        if batch["pending"] == 0:
            batch["completed_at"] = time.time()
            spawn(deliver_callback(batch))


def public(req):
    return {
        "request_id": req["id"], "model": req["model"],
        "estimated_tokens": req["tokens"], "batch_id": req["batch"],
        "status": req["status"], "attempts": req["attempts"], "error": req["error"],
        "created_at": req["created"], "completed_at": req["completed"],
        "latency_ms": round((req["completed"] - req["created"]) * 1000, 1)
        if req["completed"] else None,
        "provider_latency_ms": round((req["completed"] - req["dispatched"]) * 1000, 1)
        if req["completed"] and req["dispatched"] else None,
        "result": req["result"],
    }


def batch_summary(b):
    status = "completed" if b["pending"] == 0 else "processing"
    return {
        "batch_id": b["id"], "status": status, "total": b["total"],
        "pending": b["pending"], "succeeded": b["succeeded"],
        "failed": b["failed"] + b["rejected"] + b["expired"],
        "failed_breakdown": {"provider_failed": b["failed"],
                             "rejected": b["rejected"], "expired": b["expired"]},
        "created_at": b["created_at"], "completed_at": b["completed_at"],
        "callback": {"attempts": b["cb_attempts"], "delivered_at": b["cb_delivered"],
                     "last_error": b["cb_error"]},
    }


async def worker(model):
    p = pipelines[model]
    while True:
        req = await p["queue"].get()
        await p["sem"].acquire()

        granted = False
        while True:
            if req["deadline"] and time.time() > req["deadline"]:
                break
            if not p["limiter"].can_ever_grant(req["tokens"]):
                break
            if p["limiter"].try_acquire(req["tokens"]):
                granted = True
                break
            await asyncio.sleep(POLL)

        if not granted:
            p["sem"].release()
            if req["deadline"] and time.time() > req["deadline"]:
                finish(req, "expired", error="ttl exceeded while waiting")
            else:
                finish(req, "failed",
                       error=f"estimated_tokens={req['tokens']} exceeds what the model's "
                             f"limits can ever grant (max {p['limiter'].max_tokens()})")
            continue

        status_counts[req["status"]] -= 1
        status_counts["running"] += 1
        req["status"] = "running"
        req["dispatched"] = time.time()
        dispatch_tl[model].record(req["tokens"])
        spawn(call_provider(req, p))


async def call_provider(req, p):
    try:
        attempt = 0
        while True:
            attempt += 1
            req["attempts"] = attempt
            try:
                async with http.post(f"{PROVIDER_URL}/v1/inference", json={
                        "request_id": req["id"], "model": req["model"],
                        "estimated_tokens": req["tokens"], "payload": req["payload"]}) as resp:
                    if resp.status == 429:
                        counters["provider_429"] += 1
                        retryable = True
                    elif resp.status >= 500:
                        counters["transient_errors"] += 1
                        retryable = True
                    elif resp.status >= 400:
                        finish(req, "failed", error=f"provider returned {resp.status}")
                        return
                    else:
                        try:
                            result = await resp.json()
                        except Exception as exc:
                            finish(req, "failed", error=f"unparseable provider response: {exc}")
                            return
                        completion_tl[req["model"]].record(req["tokens"])
                        if result.get("status") == "succeeded":
                            finish(req, "succeeded", result=result)
                        else:
                            finish(req, "failed", result=result,
                                   error=result.get("error", "provider reported failure"))
                        return
            except (aiohttp.ClientError, asyncio.TimeoutError):
                counters["transient_errors"] += 1
                retryable = True
            except Exception as exc:
                finish(req, "failed", error=f"permanent provider error: {exc}")
                return

            if retryable:
                if attempt > MAX_RETRIES:
                    finish(req, "failed", error="transient errors, retries exhausted")
                    return
                if not await reacquire(req, p, attempt):
                    return
    finally:
        p["sem"].release()


async def reacquire(req, p, attempt):
    await asyncio.sleep(min(0.25 * 2 ** (attempt - 1), 5.0))
    while True:
        if req["deadline"] and time.time() > req["deadline"]:
            finish(req, "expired", error="ttl exceeded during retries")
            return False
        if not p["limiter"].can_ever_grant(req["tokens"]):
            finish(req, "failed", error="limits reduced below this request's size")
            return False
        if p["limiter"].try_acquire(req["tokens"]):
            req["dispatched"] = time.time()
            dispatch_tl[req["model"]].record(req["tokens"])
            return True
        await asyncio.sleep(POLL)


async def deliver_callback(batch):
    if not batch["callback_url"]:
        return
    for attempt in range(1, CB_ATTEMPTS + 1):
        batch["cb_attempts"] = attempt
        payload = {**batch_summary(batch),
                   "results_url": f"{PUBLIC_BASE_URL}/v1/batches/{batch['id']}/results"}
        try:
            async with http.post(batch["callback_url"], json=payload) as resp:
                if 200 <= resp.status < 300:
                    batch["cb_delivered"] = time.time()
                    batch["cb_error"] = None
                    return
                batch["cb_error"] = f"destination returned {resp.status}"
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            batch["cb_error"] = f"delivery error: {exc}"
        await asyncio.sleep(min(0.5 * 2 ** (attempt - 1), 10.0))


@asynccontextmanager
async def lifespan(app):
    global http
    http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30),
                                 connector=aiohttp.TCPConnector(limit=0))
    if os.path.exists(CONFIG):
        with open(CONFIG) as f:
            for name, cfg in json.load(f)["models"].items():
                add_model(name, cfg)
    yield
    await http.close()


def add_model(name, cfg):
    pipelines[name] = {
        "limiter": RateLimiter(cfg["rpm"], cfg["tpm"], safety=SAFETY),
        "queue": asyncio.Queue(maxsize=cfg.get("queue_size", QUEUE_SIZE)),
        "sem": asyncio.Semaphore(cfg.get("max_concurrency", MAX_CONCURRENCY)),
    }
    spawn(worker(name))


app = FastAPI(title="inference gateway", lifespan=lifespan)


def validate(item, seen_ids=None):
    model = item.get("model")
    if model not in pipelines:
        raise HTTPException(400, detail={"error": f"unknown model '{model}'",
                                         "configured_models": sorted(pipelines)})
    try:
        tokens = int(item.get("estimated_tokens", 0))
    except (TypeError, ValueError):
        tokens = 0
    if tokens <= 0:
        raise HTTPException(400, detail={"error": "estimated_tokens must be a positive integer"})
    if not pipelines[model]["limiter"].can_ever_grant(tokens):
        raise HTTPException(400, detail={
            "error": f"estimated_tokens={tokens} exceeds what '{model}' can ever dispatch",
            "max_grantable_tokens": pipelines[model]["limiter"].max_tokens()})
    rid = item.get("request_id")
    if rid is not None:
        if rid in requests or (seen_ids is not None and rid in seen_ids):
            raise HTTPException(400, detail={"error": f"duplicate request_id '{rid}'"})
        if seen_ids is not None:
            seen_ids.add(rid)


def admit(item, batch_id=None):
    req = {
        "id": item.get("request_id") or f"req-{uuid.uuid4().hex[:16]}",
        "model": item["model"], "tokens": int(item["estimated_tokens"]),
        "payload": item.get("payload"), "batch": batch_id, "status": "queued",
        "error": None, "attempts": 0, "created": time.time(),
        "dispatched": None, "completed": None, "result": None,
        "deadline": time.time() + item["ttl_seconds"] if item.get("ttl_seconds") else None,
    }
    requests[req["id"]] = req
    status_counts["queued"] += 1
    try:
        pipelines[req["model"]]["queue"].put_nowait(req)
        return req["id"], "queued"
    except asyncio.QueueFull:
        finish(req, "rejected", error="queue full: demand exceeds capacity")
        return req["id"], "rejected"


@app.post("/v1/requests", status_code=202)
async def submit_request(r: HttpRequest):
    item = await r.json()
    validate(item)
    rid, status = admit(item)
    body = {"request_id": rid, "status": status, "status_url": f"/v1/requests/{rid}"}
    return JSONResponse(body, status_code=429) if status == "rejected" else body


@app.post("/v1/batches", status_code=202)
async def submit_batch(r: HttpRequest):
    body = await r.json()
    items = body.get("requests") or []
    if not items:
        raise HTTPException(400, detail={"error": "batch must contain at least one request"})
    seen_ids = set()
    for item in items:
        validate(item, seen_ids)

    batch = {"id": f"batch-{uuid.uuid4().hex[:12]}", "total": len(items),
             "pending": len(items), "succeeded": 0, "failed": 0, "rejected": 0,
             "expired": 0, "callback_url": body.get("callback_url"),
             "created_at": time.time(), "completed_at": None, "request_ids": [],
             "cb_attempts": 0, "cb_delivered": None, "cb_error": None}
    batches[batch["id"]] = batch
    batch["request_ids"] = [admit(item, batch["id"])[0] for item in items]
    return {"batch_id": batch["id"], "total": batch["total"],
            "status": "completed" if batch["pending"] == 0 else "processing",
            "status_url": f"/v1/batches/{batch['id']}",
            "results_url": f"/v1/batches/{batch['id']}/results"}


@app.get("/v1/requests/{rid}")
async def get_request(rid: str):
    if rid not in requests:
        raise HTTPException(404, detail={"error": f"unknown request '{rid}'"})
    return public(requests[rid])


@app.post("/v1/requests/bulk_status")
async def bulk_status(r: HttpRequest):
    ids = (await r.json()).get("ids") or []
    out = {}
    for rid in ids:
        req = requests.get(rid)
        out[rid] = None if not req else {
            "status": req["status"],
            "latency_ms": round((req["completed"] - req["created"]) * 1000, 1)
            if req["completed"] else None,
            "provider_latency_ms": round((req["completed"] - req["dispatched"]) * 1000, 1)
            if req["completed"] and req["dispatched"] else None}
    return out


@app.get("/v1/batches/{bid}")
async def get_batch(bid: str):
    if bid not in batches:
        raise HTTPException(404, detail={"error": f"unknown batch '{bid}'"})
    return batch_summary(batches[bid])


@app.get("/v1/batches/{bid}/results")
async def get_batch_results(bid: str):
    if bid not in batches:
        raise HTTPException(404, detail={"error": f"unknown batch '{bid}'"})
    b = batches[bid]
    return {"batch_id": bid, "status": "completed" if b["pending"] == 0 else "processing",
            "results": [public(requests[rid]) for rid in b["request_ids"]]}


@app.put("/admin/models/{model}/limits")
async def update_limits(model: str, r: HttpRequest):
    if model not in pipelines:
        raise HTTPException(400, detail={"error": f"unknown model '{model}'"})
    body = await r.json()
    limiter = pipelines[model]["limiter"]
    limiter.update(rpm=body.get("rpm"), tpm=body.get("tpm"))
    limit_change_log.append({"at": time.time(), "model": model,
                             "rpm": limiter.rpm, "tpm": limiter.tpm})
    return {"model": model, **limiter.snapshot()}


@app.get("/health")
async def health():
    return {"status": "ok", "pid": os.getpid(),
            "uptime_seconds": round(time.time() - started_at, 1)}


@app.get("/metrics")
async def metrics():
    return {
        "pid": os.getpid(),
        "requests_by_status": dict(status_counts),
        "waiting_total": status_counts["queued"] + status_counts["running"],
        "counters": dict(counters),
        "limit_change_log": limit_change_log,
        "models": {name: {**p["limiter"].snapshot(),
                          "queue_depth": p["queue"].qsize(),
                          "worst_60s_window": dispatch_tl[name].worst_60s_window()}
                   for name, p in pipelines.items()},
    }


@app.get("/metrics/timeline")
async def timeline(model: str, kind: str = "dispatch"):
    tl = (dispatch_tl if kind == "dispatch" else completion_tl)[model]
    return {"model": model, "kind": kind, "series": tl.series(),
            "worst_60s_window": tl.worst_60s_window()}
