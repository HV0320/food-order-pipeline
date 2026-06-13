import json
import logging
import os
import socket
import time
from typing import Any

import httpx
import psycopg
import redis
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from redis.exceptions import ResponseError, TimeoutError as RedisTimeoutError

from app.state_machine import get_transition


DATABASE_URL = os.getenv("DATABASE_URL")
VALKEY_URL = os.getenv("VALKEY_URL", "redis://valkey:6379/0")
STREAM_NAME = os.getenv("STREAM_NAME", "order.workflow")
DEAD_LETTER_STREAM = os.getenv("DEAD_LETTER_STREAM", "order.dead_letter")
CONSUMER_GROUP = os.getenv("CONSUMER_GROUP", "order-workers")
CONSUMER_NAME = os.getenv("CONSUMER_NAME", socket.gethostname())

RESTAURANT_BASE_URL = os.getenv("RESTAURANT_BASE_URL", "http://restaurant-sim:8001")
COURIER_BASE_URL = os.getenv("COURIER_BASE_URL", "http://courier-sim:8002")

MAX_ATTEMPTS = int(os.getenv("MAX_ATTEMPTS", "3"))
REQUEST_TIMEOUT_SECONDS = float(os.getenv("WORKER_REQUEST_TIMEOUT_SECONDS", "3"))


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s service=worker message=%(message)s",
)

logger = logging.getLogger(__name__)


if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is not set")


redis_client = redis.Redis.from_url(
    VALKEY_URL,
    decode_responses=True,
    socket_connect_timeout=5,
    socket_timeout=15,
    health_check_interval=30,
)


DOWNSTREAM_BY_EVENT = {
    "ORDER_PLACED": f"{RESTAURANT_BASE_URL}/restaurant/confirm",
    "ORDER_CONFIRMED": f"{RESTAURANT_BASE_URL}/restaurant/start-preparation",
    "ORDER_PREPARING": f"{RESTAURANT_BASE_URL}/restaurant/mark-ready",
    "ORDER_READY": f"{COURIER_BASE_URL}/courier/assign",
    "ORDER_OUT_FOR_DELIVERY": f"{COURIER_BASE_URL}/courier/mark-delivered",
}


def get_connection():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def ensure_consumer_group():
    try:
        redis_client.xgroup_create(
            name=STREAM_NAME,
            groupname=CONSUMER_GROUP,
            id="0",
            mkstream=True,
        )

        logger.info(
            "created_consumer_group stream=%s group=%s",
            STREAM_NAME,
            CONSUMER_GROUP,
        )

    except ResponseError as exc:
        if "BUSYGROUP" in str(exc):
            logger.info(
                "consumer_group_already_exists stream=%s group=%s",
                STREAM_NAME,
                CONSUMER_GROUP,
            )
            return

        raise


def get_current_status(order_id: str) -> str | None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT status
                FROM orders
                WHERE id = %s;
                """,
                (order_id,),
            )

            row = cur.fetchone()

    if not row:
        return None

    return row["status"]


def call_downstream_once(workflow_event_type: str, order_id: str, attempt: int) -> tuple[bool, str | None]:
    url = DOWNSTREAM_BY_EVENT.get(workflow_event_type)

    if not url:
        return True, None

    payload = {
        "order_id": order_id,
        "workflow_event_type": workflow_event_type,
        "attempt": attempt,
    }

    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            response = client.post(url, json=payload)

        if 200 <= response.status_code < 300:
            logger.info(
                "downstream_success order_id=%s event_type=%s attempt=%s",
                order_id,
                workflow_event_type,
                attempt,
            )
            return True, None

        error_message = f"HTTP {response.status_code}: {response.text[:300]}"
        logger.warning(
            "downstream_failed order_id=%s event_type=%s attempt=%s error=%s",
            order_id,
            workflow_event_type,
            attempt,
            error_message,
        )
        return False, error_message

    except httpx.TimeoutException:
        error_message = "downstream timeout"
        logger.warning(
            "downstream_timeout order_id=%s event_type=%s attempt=%s",
            order_id,
            workflow_event_type,
            attempt,
        )
        return False, error_message

    except Exception as exc:
        error_message = f"downstream error: {exc}"
        logger.warning(
            "downstream_error order_id=%s event_type=%s attempt=%s error=%s",
            order_id,
            workflow_event_type,
            attempt,
            error_message,
        )
        return False, error_message


def call_downstream_with_retries(workflow_event_type: str, order_id: str) -> tuple[bool, str | None, int]:
    last_error = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        success, error_message = call_downstream_once(
            workflow_event_type=workflow_event_type,
            order_id=order_id,
            attempt=attempt,
        )

        if success:
            return True, None, attempt

        last_error = error_message

        if attempt < MAX_ATTEMPTS:
            delay_seconds = attempt
            logger.info(
                "retrying_downstream order_id=%s event_type=%s attempt=%s next_delay_seconds=%s",
                order_id,
                workflow_event_type,
                attempt,
                delay_seconds,
            )
            time.sleep(delay_seconds)

    return False, last_error or "downstream failed", MAX_ATTEMPTS


def create_next_outbox_event(cur, order_id: str, next_event_type: str, next_status: str):
    cur.execute(
        """
        INSERT INTO outbox_events (
            aggregate_type,
            aggregate_id,
            event_type,
            payload_json
        )
        VALUES ('order', %s, %s, %s);
        """,
        (
            order_id,
            next_event_type,
            Jsonb(
                {
                    "order_id": order_id,
                    "current_status": next_status,
                }
            ),
        ),
    )


def mark_order_failed(
    order_id: str,
    workflow_event_type: str,
    expected_status: str,
    error_message: str,
    attempts_used: int,
) -> str:
    with get_connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE orders
                    SET status = 'FAILED',
                        failed_reason = %s,
                        updated_at = now()
                    WHERE id = %s
                      AND status = %s
                    RETURNING id;
                    """,
                    (
                        error_message,
                        order_id,
                        expected_status,
                    ),
                )

                updated_order = cur.fetchone()

                if not updated_order:
                    logger.info(
                        "failed_update_stale order_id=%s event_type=%s expected_status=%s",
                        order_id,
                        workflow_event_type,
                        expected_status,
                    )
                    return "stale"

                cur.execute(
                    """
                    INSERT INTO order_events (
                        order_id,
                        event_type,
                        from_status,
                        to_status,
                        metadata_json
                    )
                    VALUES (%s, 'ORDER_FAILED', %s, 'FAILED', %s);
                    """,
                    (
                        order_id,
                        expected_status,
                        Jsonb(
                            {
                                "source": "worker",
                                "workflow_event_type": workflow_event_type,
                                "attempts_used": attempts_used,
                                "error": error_message,
                            }
                        ),
                    ),
                )

    redis_client.xadd(
        DEAD_LETTER_STREAM,
        {
            "order_id": order_id,
            "event_type": workflow_event_type,
            "attempts_used": str(attempts_used),
            "error": error_message,
        },
    )

    logger.error(
        "order_failed_dead_lettered order_id=%s event_type=%s attempts_used=%s error=%s",
        order_id,
        workflow_event_type,
        attempts_used,
        error_message,
    )

    return "failed"


def advance_order(order_id: str, workflow_event_type: str, stream_message_id: str) -> str:
    transition = get_transition(workflow_event_type)

    if transition is None:
        logger.warning(
            "unknown_event_type order_id=%s event_type=%s",
            order_id,
            workflow_event_type,
        )
        return "ignored"

    expected_status = transition["expected_status"]
    next_status = transition["next_status"]
    record_event_type = transition["record_event_type"]
    next_event_type = transition["next_event_type"]

    current_status = get_current_status(order_id)

    if current_status is None:
        logger.warning(
            "order_not_found order_id=%s event_type=%s",
            order_id,
            workflow_event_type,
        )
        return "missing"

    if current_status != expected_status:
        logger.info(
            "stale_or_duplicate_message order_id=%s event_type=%s expected_status=%s current_status=%s",
            order_id,
            workflow_event_type,
            expected_status,
            current_status,
        )
        return "stale"

    downstream_success, error_message, attempts_used = call_downstream_with_retries(
        workflow_event_type=workflow_event_type,
        order_id=order_id,
    )

    if not downstream_success:
        return mark_order_failed(
            order_id=order_id,
            workflow_event_type=workflow_event_type,
            expected_status=expected_status,
            error_message=error_message or "downstream failed",
            attempts_used=attempts_used,
        )

    with get_connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE orders
                    SET status = %s,
                        updated_at = now(),
                        delivered_at = CASE
                            WHEN %s = 'DELIVERED' THEN now()
                            ELSE delivered_at
                        END
                    WHERE id = %s
                      AND status = %s
                    RETURNING id;
                    """,
                    (
                        next_status,
                        next_status,
                        order_id,
                        expected_status,
                    ),
                )

                updated_order = cur.fetchone()

                if not updated_order:
                    return "stale"

                cur.execute(
                    """
                    INSERT INTO order_events (
                        order_id,
                        event_type,
                        from_status,
                        to_status,
                        metadata_json
                    )
                    VALUES (%s, %s, %s, %s, %s);
                    """,
                    (
                        order_id,
                        record_event_type,
                        expected_status,
                        next_status,
                        Jsonb(
                            {
                                "source": "worker",
                                "workflow_event_type": workflow_event_type,
                                "stream_message_id": stream_message_id,
                                "attempts_used": attempts_used,
                            }
                        ),
                    ),
                )

                if next_event_type is not None:
                    create_next_outbox_event(
                        cur=cur,
                        order_id=order_id,
                        next_event_type=next_event_type,
                        next_status=next_status,
                    )

    logger.info(
        "advanced_order order_id=%s event_type=%s from_status=%s to_status=%s attempts_used=%s",
        order_id,
        workflow_event_type,
        expected_status,
        next_status,
        attempts_used,
    )

    return "advanced"


def process_message(message_id: str, fields: dict[str, Any]) -> str:
    event_type = fields.get("event_type")
    payload_raw = fields.get("payload", "{}")
    payload = json.loads(payload_raw)
    order_id = payload.get("order_id")

    if not event_type:
        logger.warning("message_missing_event_type message_id=%s", message_id)
        return "ignored"

    if not order_id:
        logger.warning(
            "message_missing_order_id message_id=%s event_type=%s",
            message_id,
            event_type,
        )
        return "ignored"

    return advance_order(
        order_id=order_id,
        workflow_event_type=event_type,
        stream_message_id=message_id,
    )


def run_worker():
    ensure_consumer_group()

    logger.info(
        "starting_worker stream=%s group=%s consumer=%s",
        STREAM_NAME,
        CONSUMER_GROUP,
        CONSUMER_NAME,
    )

    while True:
        try:
            response = redis_client.xreadgroup(
                groupname=CONSUMER_GROUP,
                consumername=CONSUMER_NAME,
                streams={STREAM_NAME: ">"},
                count=5,
                block=5000,
            )

            if not response:
                continue

            for _stream_name, messages in response:
                for message_id, fields in messages:
                    result = process_message(message_id, fields)

                    redis_client.xack(
                        STREAM_NAME,
                        CONSUMER_GROUP,
                        message_id,
                    )

                    logger.info(
                        "acked_message message_id=%s result=%s",
                        message_id,
                        result,
                    )

        except RedisTimeoutError:
            logger.warning("valkey_read_timeout_retrying")
            time.sleep(1)

        except Exception:
            logger.exception("worker_loop_error")
            time.sleep(2)


if __name__ == "__main__":
    run_worker()
