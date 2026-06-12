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
