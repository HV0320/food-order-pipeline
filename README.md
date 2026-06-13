# Food Order Pipeline

This project builds the order pipeline for a food-delivery platform.

The system will take a customer's order from "Place Order" through to "Delivered."

## Day 1 Scope

Day 1 includes:

- FastAPI order API
- PostgreSQL database
- Docker Compose setup
- Order creation
- Order lookup
- Idempotency protection

## Run the project

```bash
cp .env.example .env
docker compose up --build

Or run in detached mode:

```bash
docker compose up --build -d
```

## Useful URLs

API docs:

```text
http://localhost:8000/docs
```

Health check:

```text
http://localhost:8000/health/live
http://localhost:8000/health/ready
```

## Current endpoints

```text
GET  /health/live
GET  /health/ready
POST /orders
GET  /orders
GET  /orders/{order_id}
GET  /orders/{order_id}/events
```

## Day 1 acceptance criteria

- Docker Compose starts API and PostgreSQL.
- POST /orders creates an order.
- GET /orders/{order_id} returns the order.
- Duplicate requests with the same Idempotency-Key return the same order.
- PostgreSQL stores orders, order items, events, and idempotency keys.

## Day 2 Scope

Day 2 adds:

- Valkey Streams
- Outbox event table
- Outbox relay service
- Worker service
- Automatic order lifecycle progression

The current automatic lifecycle is:

```text
PLACED
→ CONFIRMED
→ PREPARING
→ READY
→ OUT_FOR_DELIVERY
→ DELIVERED
```

## Day 2 services

```text
postgres
valkey
api
outbox-relay
worker
```

## Check containers

```bash
docker compose ps
```

## Check order events

```bash
docker compose exec postgres psql -U orders -d orders -c "SELECT event_type, from_status, to_status FROM order_events ORDER BY id;"
```

## Check outbox events

```bash
docker compose exec postgres psql -U orders -d orders -c "SELECT event_type, published_at, stream_message_id FROM outbox_events ORDER BY id;"
```

## Check Valkey Stream length

```bash
docker compose exec valkey valkey-cli XLEN order.workflow
```
