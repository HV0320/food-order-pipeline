import asyncio
import json
import os
import random
import time
from typing import Any

import httpx
import psycopg
import redis
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from psycopg.rows import dict_row


DATABASE_URL = os.getenv("DATABASE_URL")
VALKEY_URL = os.getenv("VALKEY_URL", "redis://valkey:6379/0")
STREAM_NAME = os.getenv("STREAM_NAME", "order.workflow")
DEAD_LETTER_STREAM = os.getenv("DEAD_LETTER_STREAM", "order.dead_letter")
CONSUMER_GROUP = os.getenv("CONSUMER_GROUP", "order-workers")
RESTAURANT_BASE_URL = os.getenv("RESTAURANT_BASE_URL", "http://restaurant-sim:8001")
COURIER_BASE_URL = os.getenv("COURIER_BASE_URL", "http://courier-sim:8002")


router = APIRouter()


ORDER_STATUSES = [
    "PLACED",
    "CONFIRMED",
    "PREPARING",
    "READY",
    "OUT_FOR_DELIVERY",
    "DELIVERED",
    "CANCELLED",
    "FAILED",
]


redis_client = redis.Redis.from_url(
    VALKEY_URL,
    decode_responses=True,
    socket_connect_timeout=2,
    socket_timeout=5,
    health_check_interval=30,
)


def get_connection():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL environment variable is not set")

    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def serialize_order(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "order_id": str(row["id"]),
        "client_order_id": row["client_order_id"],
        "restaurant_id": row["restaurant_id"],
        "status": row["status"],
        "total_amount": float(row["total_amount"]),
        "failed_reason": row.get("failed_reason"),
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
    }


def safe_valkey_metrics() -> dict[str, Any]:
    result = {
        "workflow_stream_length": None,
        "dead_letter_count": None,
        "pending_messages": None,
        "consumer_group_lag": None,
        "error": None,
    }

    try:
        result["workflow_stream_length"] = redis_client.xlen(STREAM_NAME)
    except Exception as exc:
        result["error"] = str(exc)

    try:
        result["dead_letter_count"] = redis_client.xlen(DEAD_LETTER_STREAM)
    except Exception as exc:
        result["error"] = str(exc)

    try:
        pending = redis_client.xpending(STREAM_NAME, CONSUMER_GROUP)
        if isinstance(pending, dict):
            result["pending_messages"] = pending.get("pending", 0)
        else:
            result["pending_messages"] = 0
    except Exception:
        result["pending_messages"] = 0

    try:
        groups = redis_client.xinfo_groups(STREAM_NAME)

        for group in groups:
            if group.get("name") == CONSUMER_GROUP:
                result["consumer_group_lag"] = group.get("lag")
                break

        if result["consumer_group_lag"] is None:
            result["consumer_group_lag"] = 0

    except Exception:
        result["consumer_group_lag"] = 0

    return result


def safe_downstream_health() -> dict[str, Any]:
    health = {
        "restaurant": {"status": "unknown", "error": None},
        "courier": {"status": "unknown", "error": None},
    }

    try:
        with httpx.Client(timeout=2) as client:
            response = client.get(f"{RESTAURANT_BASE_URL}/restaurant/health")
            response.raise_for_status()
            health["restaurant"] = response.json()
    except Exception as exc:
        health["restaurant"] = {"status": "unreachable", "error": str(exc)}

    try:
        with httpx.Client(timeout=2) as client:
            response = client.get(f"{COURIER_BASE_URL}/courier/health")
            response.raise_for_status()
            health["courier"] = response.json()
    except Exception as exc:
        health["courier"] = {"status": "unreachable", "error": str(exc)}

    return health


def get_dashboard_summary() -> dict[str, Any]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*) AS total_orders,
                    COUNT(*) FILTER (
                        WHERE status NOT IN ('DELIVERED', 'CANCELLED', 'FAILED')
                    ) AS active_orders,
                    COUNT(*) FILTER (WHERE status = 'DELIVERED') AS delivered_orders,
                    COUNT(*) FILTER (WHERE status = 'FAILED') AS failed_orders,
                    COUNT(*) FILTER (
                        WHERE created_at >= now() - interval '60 seconds'
                    ) AS orders_created_last_minute,
                    COUNT(*) FILTER (
                        WHERE delivered_at >= now() - interval '60 seconds'
                    ) AS orders_delivered_last_minute,
                    COALESCE(
                        ROUND(AVG(EXTRACT(EPOCH FROM delivered_at - created_at))::numeric, 2),
                        0
                    ) AS avg_delivery_seconds
                FROM orders;
                """
            )
            totals = cur.fetchone()

            cur.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM orders
                GROUP BY status;
                """
            )
            status_rows = cur.fetchall()

            status_counts = {status: 0 for status in ORDER_STATUSES}
            for row in status_rows:
                status_counts[row["status"]] = row["count"]

            cur.execute(
                """
                SELECT COUNT(*) AS unpublished_outbox_events
                FROM outbox_events
                WHERE published_at IS NULL;
                """
            )
            outbox_row = cur.fetchone()

            cur.execute(
                """
                SELECT COUNT(*) AS duplicate_client_order_ids
                FROM (
                    SELECT client_order_id
                    FROM orders
                    GROUP BY client_order_id
                    HAVING COUNT(*) > 1
                ) duplicates;
                """
            )
            duplicate_row = cur.fetchone()

            cur.execute(
                """
                SELECT
                    id,
                    client_order_id,
                    restaurant_id,
                    status,
                    total_amount,
                    failed_reason,
                    created_at,
                    updated_at
                FROM orders
                ORDER BY created_at DESC
                LIMIT 20;
                """
            )
            recent_orders = [serialize_order(row) for row in cur.fetchall()]

            cur.execute(
                """
                SELECT
                    id,
                    client_order_id,
                    restaurant_id,
                    status,
                    total_amount,
                    failed_reason,
                    created_at,
                    updated_at
                FROM orders
                WHERE status = 'FAILED'
                ORDER BY updated_at DESC
                LIMIT 15;
                """
            )
            recent_failures = [serialize_order(row) for row in cur.fetchall()]

            cur.execute(
                """
                SELECT
                    id,
                    client_order_id,
                    restaurant_id,
                    status,
                    total_amount,
                    failed_reason,
                    created_at,
                    updated_at
                FROM orders
                WHERE status NOT IN ('DELIVERED', 'CANCELLED', 'FAILED')
                  AND updated_at < now() - interval '30 seconds'
                ORDER BY updated_at ASC
                LIMIT 15;
                """
            )
            stuck_orders = [serialize_order(row) for row in cur.fetchall()]

            cur.execute(
                """
                SELECT
                    restaurant_id,
                    COUNT(*) AS total_orders,
                    COUNT(*) FILTER (WHERE status = 'DELIVERED') AS delivered_orders,
                    COUNT(*) FILTER (WHERE status = 'FAILED') AS failed_orders,
                    COUNT(*) FILTER (
                        WHERE status NOT IN ('DELIVERED', 'CANCELLED', 'FAILED')
                    ) AS active_orders,
                    ROUND(
                        (
                            100.0 * COUNT(*) FILTER (WHERE status = 'FAILED')
                            / NULLIF(COUNT(*), 0)
                        )::numeric,
                        1
                    ) AS failure_rate_percent
                FROM orders
                GROUP BY restaurant_id
                ORDER BY total_orders DESC
                LIMIT 10;
                """
            )
            restaurant_metrics = [
                {
                    "restaurant_id": row["restaurant_id"],
                    "total_orders": row["total_orders"],
                    "delivered_orders": row["delivered_orders"],
                    "failed_orders": row["failed_orders"],
                    "active_orders": row["active_orders"],
                    "failure_rate_percent": float(row["failure_rate_percent"] or 0),
                }
                for row in cur.fetchall()
            ]

            cur.execute(
                """
                SELECT
                    o.client_order_id,
                    o.restaurant_id,
                    e.event_type,
                    e.from_status,
                    e.to_status,
                    e.created_at
                FROM order_events e
                JOIN orders o ON o.id = e.order_id
                ORDER BY e.created_at DESC
                LIMIT 30;
                """
            )
            recent_events = [
                {
                    "client_order_id": row["client_order_id"],
                    "restaurant_id": row["restaurant_id"],
                    "event_type": row["event_type"],
                    "from_status": row["from_status"],
                    "to_status": row["to_status"],
                    "created_at": row["created_at"].isoformat(),
                }
                for row in cur.fetchall()
            ]

    valkey_metrics = safe_valkey_metrics()

    return {
        "timestamp": int(time.time()),
        "summary": {
            "total_orders": totals["total_orders"],
            "active_orders": totals["active_orders"],
            "delivered_orders": totals["delivered_orders"],
            "failed_orders": totals["failed_orders"],
            "orders_created_last_minute": totals["orders_created_last_minute"],
            "orders_delivered_last_minute": totals["orders_delivered_last_minute"],
            "avg_delivery_seconds": float(totals["avg_delivery_seconds"]),
            "duplicate_client_order_ids": duplicate_row["duplicate_client_order_ids"],
        },
        "status_counts": status_counts,
        "queue": {
            "workflow_stream_length": valkey_metrics["workflow_stream_length"],
            "dead_letter_count": valkey_metrics["dead_letter_count"],
            "pending_messages": valkey_metrics["pending_messages"],
            "consumer_group_lag": valkey_metrics["consumer_group_lag"],
            "unpublished_outbox_events": outbox_row["unpublished_outbox_events"],
            "valkey_error": valkey_metrics["error"],
        },
        "downstream": safe_downstream_health(),
        "recent_orders": recent_orders,
        "recent_failures": recent_failures,
        "stuck_orders": stuck_orders,
        "restaurant_metrics": restaurant_metrics,
        "recent_events": recent_events,
    }


@router.get("/dashboard/summary")
def dashboard_summary():
    return get_dashboard_summary()


@router.get("/dashboard/stream")
async def dashboard_stream():
    async def event_generator():
        while True:
            data = get_dashboard_summary()
            yield f"data: {json.dumps(data, default=str)}\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


async def post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            return response.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/control/restaurant/healthy")
async def restaurant_healthy():
    return await post_json(
        f"{RESTAURANT_BASE_URL}/restaurant/config",
        {
            "min_latency_ms": 100,
            "max_latency_ms": 300,
            "failure_rate": 0,
            "timeout_rate": 0,
            "rate_limit_per_second": 100,
        },
    )


@router.post("/control/restaurant/degraded")
async def restaurant_degraded():
    return await post_json(
        f"{RESTAURANT_BASE_URL}/restaurant/config",
        {
            "min_latency_ms": 300,
            "max_latency_ms": 1500,
            "failure_rate": 0.45,
            "timeout_rate": 0.05,
            "rate_limit_per_second": 50,
        },
    )


@router.post("/control/restaurant/down")
async def restaurant_down():
    return await post_json(
        f"{RESTAURANT_BASE_URL}/restaurant/config",
        {
            "min_latency_ms": 100,
            "max_latency_ms": 300,
            "failure_rate": 1,
            "timeout_rate": 0,
            "rate_limit_per_second": 100,
        },
    )


@router.post("/control/courier/healthy")
async def courier_healthy():
    return await post_json(
        f"{COURIER_BASE_URL}/courier/config",
        {
            "min_latency_ms": 100,
            "max_latency_ms": 300,
            "failure_rate": 0,
            "timeout_rate": 0,
            "rate_limit_per_second": 100,
            "no_courier_available_rate": 0,
        },
    )


@router.post("/control/courier/degraded")
async def courier_degraded():
    return await post_json(
        f"{COURIER_BASE_URL}/courier/config",
        {
            "min_latency_ms": 300,
            "max_latency_ms": 1800,
            "failure_rate": 0.35,
            "timeout_rate": 0.05,
            "rate_limit_per_second": 50,
            "no_courier_available_rate": 0.20,
        },
    )


@router.post("/control/courier/down")
async def courier_down():
    return await post_json(
        f"{COURIER_BASE_URL}/courier/config",
        {
            "min_latency_ms": 100,
            "max_latency_ms": 300,
            "failure_rate": 1,
            "timeout_rate": 0,
            "rate_limit_per_second": 100,
            "no_courier_available_rate": 1,
        },
    )
