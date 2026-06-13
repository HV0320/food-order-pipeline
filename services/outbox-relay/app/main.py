import json
import logging
import os
import time
from typing import Any

import psycopg
import redis
from psycopg.rows import dict_row


DATABASE_URL = os.getenv("DATABASE_URL")
VALKEY_URL = os.getenv("VALKEY_URL", "redis://valkey:6379/0")
STREAM_NAME = os.getenv("STREAM_NAME", "order.workflow")
POLL_INTERVAL_SECONDS = float(os.getenv("POLL_INTERVAL_SECONDS", "0.5"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "25"))


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s service=outbox-relay message=%(message)s",
)

logger = logging.getLogger(__name__)


if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is not set")


redis_client = redis.Redis.from_url(VALKEY_URL, decode_responses=True)


def get_connection():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def to_json(value: Any) -> str:
    return json.dumps(value, default=str, separators=(",", ":"))


def publish_batch() -> int:
    published_count = 0

    with get_connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        id,
                        aggregate_type,
                        aggregate_id,
                        event_type,
                        payload_json
                    FROM outbox_events
                    WHERE published_at IS NULL
                    ORDER BY id ASC
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED;
                    """,
                    (BATCH_SIZE,),
                )

                rows = cur.fetchall()

                for row in rows:
                    message_id = redis_client.xadd(
                        STREAM_NAME,
                        {
                            "outbox_id": str(row["id"]),
                            "aggregate_type": row["aggregate_type"],
                            "aggregate_id": str(row["aggregate_id"]),
                            "event_type": row["event_type"],
                            "payload": to_json(row["payload_json"]),
                        },
                    )

                    cur.execute(
                        """
                        UPDATE outbox_events
                        SET published_at = now(),
                            stream_message_id = %s
                        WHERE id = %s;
                        """,
                        (message_id, row["id"]),
                    )

                    published_count += 1

                    logger.info(
                        "published_outbox_event outbox_id=%s event_type=%s stream_message_id=%s",
                        row["id"],
                        row["event_type"],
                        message_id,
                    )

    return published_count


def main():
    logger.info("starting_outbox_relay stream=%s", STREAM_NAME)

    while True:
        try:
            published_count = publish_batch()

            if published_count == 0:
                time.sleep(POLL_INTERVAL_SECONDS)

        except Exception:
            logger.exception("outbox_relay_error")
            time.sleep(2)


if __name__ == "__main__":
    main()
