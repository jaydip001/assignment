from __future__ import annotations

import asyncio
import json
import os
import random
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request as HttpRequest
from fastapi.responses import JSONResponse

from service import RateLimiter, Timeline

CONFIG = os.environ.get("CONFIG", "config.json")
DEFAULTS = {"rpm": 50_000, "tpm": 100_000_000, "latency_ms": [80, 250],
            "failure_rate": 0.0, "transient_rate": 0.0}

models: dict[str, dict] = {}


def add_model(name, cfg):
    cfg = {**DEFAULTS, **cfg}
    models[name] = {
        "cfg": cfg,
        "limiter": RateLimiter(cfg["rpm"], cfg["tpm"], burst_seconds=10.0),
        "timeline": Timeline(),
        "stats": {"accepted": 0, "rejected_429": 0, "succeeded": 0,
                  "failed": 0, "transient_503": 0},
    }


@asynccontextmanager
async def lifespan(app):
    if os.path.exists(CONFIG):
        with open(CONFIG) as f:
            for name, cfg in json.load(f)["models"].items():
                add_model(name, cfg)
    yield


app = FastAPI(title="provider simulator", lifespan=lifespan)


@app.post("/v1/inference")
async def inference(r: HttpRequest):
    body = await r.json()
    m = models.get(body.get("model"))
    if m is None:
        raise HTTPException(400, detail={"error": f"unknown model '{body.get('model')}'"})
    tokens = int(body.get("estimated_tokens", 0))

    if not m["limiter"].try_acquire(tokens):
        m["stats"]["rejected_429"] += 1
        return JSONResponse({"error": "rate limit exceeded"}, status_code=429)

    m["stats"]["accepted"] += 1
    m["timeline"].record(tokens)
    cfg = m["cfg"]

    if cfg["transient_rate"] and random.random() < cfg["transient_rate"]:
        m["stats"]["transient_503"] += 1
        return JSONResponse({"error": "temporarily overloaded, retry"}, status_code=503)

    latency_ms = random.uniform(*cfg["latency_ms"])
    await asyncio.sleep(latency_ms / 1000)

    if cfg["failure_rate"] and random.random() < cfg["failure_rate"]:
        m["stats"]["failed"] += 1
        return {"request_id": body.get("request_id"), "status": "failed",
                "error": "simulated permanent failure", "latency_ms": round(latency_ms, 1)}

    m["stats"]["succeeded"] += 1
    return {"request_id": body.get("request_id"), "status": "succeeded",
            "tokens_used": tokens, "latency_ms": round(latency_ms, 1)}


@app.put("/admin/models/{name}")
async def upsert_model(name: str, r: HttpRequest):
    cfg = await r.json()
    if name in models:
        models[name]["cfg"].update(cfg)
        models[name]["limiter"].update(rpm=models[name]["cfg"]["rpm"],
                                       tpm=models[name]["cfg"]["tpm"])
    else:
        add_model(name, cfg)
    return {"model": name, **models[name]["cfg"]}


@app.get("/admin/stats")
async def stats():
    return {name: {"config": m["cfg"], "stats": m["stats"],
                   "worst_60s_window_observed": m["timeline"].worst_60s_window()}
            for name, m in models.items()}


@app.get("/health")
async def health():
    return {"status": "ok", "pid": os.getpid()}
