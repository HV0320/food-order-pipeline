import asyncio
import random
import time
from collections import deque
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


app = FastAPI(
    title="Courier Simulator",
    version="0.1.0",
)


class SimulatorConfig(BaseModel):
    min_latency_ms: int = Field(default=100, ge=0)
    max_latency_ms: int = Field(default=300, ge=0)
    failure_rate: float = Field(default=0, ge=0, le=1)
    timeout_rate: float = Field(default=0, ge=0, le=1)
    rate_limit_per_second: int = Field(default=100, ge=1)
    no_courier_available_rate: float = Field(default=0, ge=0, le=1)


config = SimulatorConfig()
request_timestamps = deque()

counters = {
    "requests_total": 0,
    "success_total": 0,
    "failures_total": 0,
    "timeouts_total": 0,
    "rate_limited_total": 0,
    "no_courier_available_total": 0,
}


def is_rate_limited() -> bool:
    now = time.time()

    while request_timestamps and request_timestamps[0] < now - 1:
        request_timestamps.popleft()

    if len(request_timestamps) >= config.rate_limit_per_second:
        return True

    request_timestamps.append(now)
    return False


async def simulate_operation(stage: str, payload: dict[str, Any]):
    counters["requests_total"] += 1

    if is_rate_limited():
        counters["rate_limited_total"] += 1
        raise HTTPException(
            status_code=429,
            detail=f"Courier rate limited stage={stage}",
        )

    latency_ms = random.randint(
        config.min_latency_ms,
        max(config.max_latency_ms, config.min_latency_ms),
    )

    if random.random() < config.timeout_rate:
        counters["timeouts_total"] += 1
        await asyncio.sleep(8)
        return {
            "status": "late_success",
            "stage": stage,
            "order_id": payload.get("order_id"),
        }

    await asyncio.sleep(latency_ms / 1000)

    if stage == "assign" and random.random() < config.no_courier_available_rate:
        counters["no_courier_available_total"] += 1
        raise HTTPException(
            status_code=503,
            detail="No courier available right now.",
        )

    if random.random() < config.failure_rate:
        counters["failures_total"] += 1
        raise HTTPException(
            status_code=500,
            detail=f"Courier failed stage={stage}",
        )

    counters["success_total"] += 1

    return {
        "status": "success",
        "stage": stage,
        "order_id": payload.get("order_id"),
        "latency_ms": latency_ms,
    }


@app.get("/courier/health")
def health():
    return {
        "status": "ok",
        "config": config.model_dump(),
        "counters": counters,
    }


@app.post("/courier/config")
def update_config(new_config: SimulatorConfig):
    global config
    config = new_config
    return {
        "status": "updated",
        "config": config.model_dump(),
    }


@app.post("/courier/assign")
async def assign(payload: dict[str, Any]):
    return await simulate_operation("assign", payload)


@app.post("/courier/mark-delivered")
async def mark_delivered(payload: dict[str, Any]):
    return await simulate_operation("mark-delivered", payload)
