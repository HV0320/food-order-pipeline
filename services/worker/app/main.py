import json
import logging
import os
import socket
import time
from typing import Any

import psycopg
import redis
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from redis.exceptions import ResponseError

from app.state_machine import get_transition


DATABASE_URL = os.getenv("DATABASE_URL")
VALKEY_URL = os.getenv("VALKEY_URL", "redis://valkey:6379/0")
STREAM_NAME = os.getenv("STREAM_NAME", "order.workflow")
CONSUMER_GROUP = os.getenv("CONSUMER_GROUP", "order-workers")
CONSUMER_NAME = os.getenv("CONSUMER_NAME", socket.gethostname())


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s service=worker message=%(message)s",
)

logger = logging.getLogger(__name__)


if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is not set")


redis_client = redis.Redis.from_url(VALKEY_URL, decode_responses=True)


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


def create_next_outbox_event(
    cur,
    order_id: str,
    next_event_type: str,
    next_status: str,
):
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


def advance_order(order_id: str, workflow_event_type: str, stream_message_id: str) -> str:
    transition = get_transition(workflow_event_type)

    if transition is None:
        logger.warning(
            "unknown_event_type event_type=%s order_id=%s",
            workflow_event_type,
            order_id,
        )
        return "ignored"

    expected_status = transition["expected_status"]
    next_status = transition["next_status"]
    record_event_type = transition["record_event_type"]
    next_event_type = transition["next_event_type"]

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
                    RETURNING id, status;
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
                    cur.execute(
                        """
                        SELECT status
                        FROM orders
                        WHERE id = %s;
                        """,
                        (order_id,),
                    )

                    current_order = cur.fetchone()

                    if current_order:
                        logger.info(
                            "stale_or_duplicate_message order_id=%s event_type=%s expected_status=%s current_status=%s",
                            order_id,
                            workflow_event_type,
                            expected_status,
                            current_order["status"],
                        )
                        return "stale"

                    logger.warning(
                        "order_not_found order_id=%s event_type=%s",
                        order_id,
                        workflow_event_type,
                    )
                    return "missing"

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
        "advanced_order order_id=%s workflow_event_type=%s record_event_type=%s from_status=%s to_status=%s next_event_type=%s",
        order_id,
        workflow_event_type,
        record_event_type,
        expected_status,
        next_status,
        next_event_type,
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

        except Exception:
            logger.exception("worker_loop_error")
            time.sleep(2)


if __name__ == "__main__":
    run_worker()
