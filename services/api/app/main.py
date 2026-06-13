import hashlib
import json
import os
from typing import Any
from uuid import UUID

import psycopg
from fastapi import FastAPI, Header, HTTPException, Query, Response, status
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field


DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is not set")


app = FastAPI(
    title="Food Order Pipeline API",
    version="0.2.0",
    description="Food-delivery order API with outbox event support.",
)


class DeliveryAddress(BaseModel):
    line1: str = Field(min_length=1)
    city: str = Field(min_length=1)
    postcode: str = Field(min_length=1)


class OrderItemIn(BaseModel):
    name: str = Field(min_length=1)
    quantity: int = Field(gt=0)
    price: float = Field(ge=0)


class OrderCreate(BaseModel):
    client_order_id: str = Field(min_length=1)
    customer_id: str = Field(min_length=1)
    restaurant_id: str = Field(min_length=1)
    items: list[OrderItemIn] = Field(min_length=1)
    delivery_address: DeliveryAddress


class OrderSummary(BaseModel):
    order_id: str
    client_order_id: str
    status: str
    total_amount: float
    created_at: str


class OrderItemOut(BaseModel):
    name: str
    quantity: int
    price: float


class OrderDetail(OrderSummary):
    customer_id: str
    restaurant_id: str
    delivery_address: dict[str, Any]
    items: list[OrderItemOut]


def get_connection():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def hash_order_request(order: OrderCreate) -> str:
    payload = order.model_dump(mode="json")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def order_summary_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "order_id": str(row["id"]),
        "client_order_id": row["client_order_id"],
        "status": row["status"],
        "total_amount": float(row["total_amount"]),
        "created_at": row["created_at"].isoformat(),
    }


@app.get("/health/live")
def health_live():
    return {"status": "alive"}


@app.get("/health/ready")
def health_ready():
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 AS ok;")
                cur.fetchone()

        return {"status": "ready"}
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Database is not ready: {exc}",
        )


@app.post("/orders", response_model=OrderSummary, status_code=status.HTTP_201_CREATED)
def create_order(
    order: OrderCreate,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    key = idempotency_key or order.client_order_id
    request_hash = hash_order_request(order)
    total_amount = round(sum(item.quantity * item.price for item in order.items), 2)

    try:
        with get_connection() as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT idempotency_key, request_hash, response_json
                        FROM idempotency_keys
                        WHERE idempotency_key = %s;
                        """,
                        (key,),
                    )
                    existing_key = cur.fetchone()

                    if existing_key:
                        if existing_key["request_hash"] != request_hash:
                            raise HTTPException(
                                status_code=status.HTTP_409_CONFLICT,
                                detail="The same Idempotency-Key was used with a different request body.",
                            )

                        response.status_code = status.HTTP_200_OK
                        return existing_key["response_json"]

                    cur.execute(
                        """
                        INSERT INTO orders (
                            client_order_id,
                            customer_id,
                            restaurant_id,
                            status,
                            total_amount,
                            delivery_address
                        )
                        VALUES (%s, %s, %s, 'PLACED', %s, %s)
                        ON CONFLICT (client_order_id) DO NOTHING
                        RETURNING id, client_order_id, status, total_amount, created_at;
                        """,
                        (
                            order.client_order_id,
                            order.customer_id,
                            order.restaurant_id,
                            total_amount,
                            Jsonb(order.delivery_address.model_dump(mode="json")),
                        ),
                    )

                    order_row = cur.fetchone()
                    created_new_order = order_row is not None

                    if not created_new_order:
                        cur.execute(
                            """
                            SELECT id, client_order_id, status, total_amount, created_at
                            FROM orders
                            WHERE client_order_id = %s;
                            """,
                            (order.client_order_id,),
                        )
                        order_row = cur.fetchone()
                        response.status_code = status.HTTP_200_OK

                    if not order_row:
                        raise HTTPException(
                            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail="Could not create or find order.",
                        )

                    if created_new_order:
                        for item in order.items:
                            cur.execute(
                                """
                                INSERT INTO order_items (
                                    order_id,
                                    name,
                                    quantity,
                                    price
                                )
                                VALUES (%s, %s, %s, %s);
                                """,
                                (
                                    order_row["id"],
                                    item.name,
                                    item.quantity,
                                    item.price,
                                ),
                            )

                        cur.execute(
                            """
                            INSERT INTO order_events (
                                order_id,
                                event_type,
                                from_status,
                                to_status,
                                metadata_json
                            )
                            VALUES (%s, 'ORDER_PLACED', NULL, 'PLACED', %s);
                            """,
                            (
                                order_row["id"],
                                Jsonb({"source": "api"}),
                            ),
                        )

                        cur.execute(
                            """
                            INSERT INTO outbox_events (
                                aggregate_type,
                                aggregate_id,
                                event_type,
                                payload_json
                            )
                            VALUES ('order', %s, 'ORDER_PLACED', %s);
                            """,
                            (
                                order_row["id"],
                                Jsonb(
                                    {
                                        "order_id": str(order_row["id"]),
                                        "client_order_id": order.client_order_id,
                                        "current_status": "PLACED",
                                    }
                                ),
                            ),
                        )

                    response_body = order_summary_from_row(order_row)

                    cur.execute(
                        """
                        INSERT INTO idempotency_keys (
                            idempotency_key,
                            request_hash,
                            order_id,
                            response_json
                        )
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (idempotency_key) DO NOTHING;
                        """,
                        (
                            key,
                            request_hash,
                            order_row["id"],
                            Jsonb(response_body),
                        ),
                    )

                    return response_body

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        )


@app.get("/orders", response_model=list[OrderSummary])
def list_orders(limit: int = Query(default=50, ge=1, le=200)):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, client_order_id, status, total_amount, created_at
                FROM orders
                ORDER BY created_at DESC
                LIMIT %s;
                """,
                (limit,),
            )

            rows = cur.fetchall()

    return [order_summary_from_row(row) for row in rows]


@app.get("/orders/{order_id}", response_model=OrderDetail)
def get_order(order_id: UUID):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    id,
                    client_order_id,
                    customer_id,
                    restaurant_id,
                    status,
                    total_amount,
                    delivery_address,
                    created_at
                FROM orders
                WHERE id = %s;
                """,
                (order_id,),
            )

            order_row = cur.fetchone()

            if not order_row:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Order not found.",
                )

            cur.execute(
                """
                SELECT name, quantity, price
                FROM order_items
                WHERE order_id = %s
                ORDER BY id;
                """,
                (order_id,),
            )

            item_rows = cur.fetchall()

    summary = order_summary_from_row(order_row)

    return {
        **summary,
        "customer_id": order_row["customer_id"],
        "restaurant_id": order_row["restaurant_id"],
        "delivery_address": order_row["delivery_address"],
        "items": [
            {
                "name": item["name"],
                "quantity": item["quantity"],
                "price": float(item["price"]),
            }
            for item in item_rows
        ],
    }


@app.get("/orders/{order_id}/events")
def get_order_events(order_id: UUID):
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    id,
                    event_type,
                    from_status,
                    to_status,
                    metadata_json,
                    created_at
                FROM order_events
                WHERE order_id = %s
                ORDER BY id ASC;
                """,
                (order_id,),
            )

            rows = cur.fetchall()

    return [
        {
            "id": row["id"],
            "event_type": row["event_type"],
            "from_status": row["from_status"],
            "to_status": row["to_status"],
            "metadata_json": row["metadata_json"],
            "created_at": row["created_at"].isoformat(),
        }
        for row in rows
    ]
