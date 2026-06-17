# Food Order Pipeline

A single-machine Docker Compose project that models a food-delivery order pipeline from order placement through delivery, including bursty load, flaky restaurant/courier downstreams, retries, cancellation, dead-letter handling, worker scaling, and a live React dashboard.

## Quick Start

```bash
cp .env.example .env
docker compose up --build -d
```

Check services:

```bash
docker compose ps
```

Open:

| Service | URL |
|---|---|
| React dashboard | http://localhost:5173 |
| API docs | http://localhost:8000/docs |
| Restaurant simulator docs | http://localhost:8001/docs |
| Courier simulator docs | http://localhost:8002/docs |
| Locust load generator | http://localhost:8089 |

Health checks:

```bash
curl http://localhost:8000/health/live
curl http://localhost:8000/health/ready
```

## What the System Does

Orders move through this lifecycle:

```text
PLACED → CONFIRMED → PREPARING → READY → OUT_FOR_DELIVERY → DELIVERED
```

Orders can also end as:

```text
CANCELLED
FAILED
```

Cancellation is supported before dispatch:

```text
PLACED, CONFIRMED, PREPARING, READY → CANCELLED
```

Cancellation is rejected once an order is out for delivery, delivered, failed, or already cancelled.

## Main Services

| Service | Purpose |
|---|---|
| `api` | FastAPI Order API, dashboard summary/SSE stream, simulator control endpoints |
| `postgres` | Source of truth for orders, events, idempotency, and outbox events |
| `valkey` | Redis-compatible stream/queue for workflow messages |
| `outbox-relay` | Publishes durable outbox events from PostgreSQL to Valkey Streams |
| `worker` | Consumes workflow messages, calls downstreams, advances lifecycle |
| `restaurant-sim` | Simulated flaky restaurant integration |
| `courier-sim` | Simulated flaky courier integration |
| `frontend` | React + Vite live dashboard and demo controls |
| `loadgen` | Locust load generator for dinner-rush traffic |

## Dashboard

Open:

```text
http://localhost:5173
```

The React dashboard updates live using SSE and shows:

- order counts and lifecycle funnel
- created/delivered throughput
- average and p95 delivery latency
- stage latency by lifecycle state
- restaurant/courier simulator health
- top restaurants by volume/failures
- stuck/slow active orders
- recent orders, failures, and events
- dead-letter count, consumer group lag, pending messages, and estimated technical backlog

The dashboard also includes buttons to:

- create one demo order
- create small bursts of orders
- set all restaurant integrations healthy/degraded/down-like
- set courier dispatch healthy/degraded/down-like
- cancel cancellable orders from the recent orders table

## Drive Load

Open Locust:

```text
http://localhost:8089
```

Use:

```text
Host: http://api:8000
```

Suggested scenarios:

| Scenario | Users | Spawn rate |
|---|---:|---:|
| Normal traffic | 10 | 2 |
| Dinner rush | 50 | 10 |
| Promotion spike | 100 | 20 |

If running in a smaller environment, use `50` users and `10` spawn rate for the dinner-rush demo.

## Trigger Failures

You can trigger downstream failures from the React dashboard, or directly through the simulator APIs.

Restaurant degraded:

```bash
curl -X POST "http://localhost:8001/restaurant/config" \
  -H "Content-Type: application/json" \
  -d '{
    "min_latency_ms": 300,
    "max_latency_ms": 1500,
    "failure_rate": 0.45,
    "timeout_rate": 0.05,
    "rate_limit_per_second": 50
  }'
```

Restaurant recovery:

```bash
curl -X POST "http://localhost:8001/restaurant/config" \
  -H "Content-Type: application/json" \
  -d '{
    "min_latency_ms": 100,
    "max_latency_ms": 300,
    "failure_rate": 0,
    "timeout_rate": 0,
    "rate_limit_per_second": 100
  }'
```

Courier degraded:

```bash
curl -X POST "http://localhost:8002/courier/config" \
  -H "Content-Type: application/json" \
  -d '{
    "min_latency_ms": 300,
    "max_latency_ms": 1800,
    "failure_rate": 0.35,
    "timeout_rate": 0.05,
    "rate_limit_per_second": 50,
    "no_courier_available_rate": 0.20
  }'
```

Courier recovery:

```bash
curl -X POST "http://localhost:8002/courier/config" \
  -H "Content-Type: application/json" \
  -d '{
    "min_latency_ms": 100,
    "max_latency_ms": 300,
    "failure_rate": 0,
    "timeout_rate": 0,
    "rate_limit_per_second": 100,
    "no_courier_available_rate": 0
  }'
```

## Worker Failure and Scaling

Stop workers while traffic is running:

```bash
docker compose kill worker
```

Restart one worker:

```bash
docker compose up -d --scale worker=1
```

Scale workers:

```bash
docker compose up -d --scale worker=3
```

Scale back down:

```bash
docker compose up -d --scale worker=1
```

The dashboard should show active orders/backlog rising when workers are stopped, and draining when workers are restarted or scaled.

## Useful Debug Commands

```bash
docker compose ps
docker compose logs --tail=100 worker
docker compose logs --tail=100 outbox-relay
docker compose exec valkey valkey-cli XINFO GROUPS order.workflow
docker compose exec valkey valkey-cli XLEN order.dead_letter
docker compose exec valkey valkey-cli XRANGE order.dead_letter - +
```

Check order state directly:

```bash
docker compose exec postgres psql -U orders -d orders -c "
SELECT status, COUNT(*)
FROM orders
GROUP BY status
ORDER BY status;
"
```

## Clean Reset for Demo

This deletes local data and starts fresh:

```bash
docker compose down -v
docker compose up --build -d
```

Use this before a clean final demo if you want the dashboard to start from zero.

## Architecture Notes

- PostgreSQL is the source of truth for order state.
- The API accepts orders quickly and writes an outbox event in the same database transaction.
- The outbox relay publishes workflow events to Valkey Streams.
- Workers consume from one generic `order-workers` consumer group and advance orders one lifecycle step at a time.
- Restaurant and courier simulators are representative downstream integrations with configurable global health profiles.
- Orders still use many virtual `restaurant_id`s, so the dashboard can show restaurant-level business impact.
- The dashboard uses SSE for server-to-browser updates; buttons use normal HTTP POST requests.

## Main Design Decisions and Trade-offs

- Used at-least-once processing with idempotent, guarded state transitions instead of claiming exactly-once processing.
- Used the outbox pattern to avoid saving an order without also recording workflow work to publish.
- Used Valkey Streams instead of plain Redis Pub/Sub so messages are durable, acknowledged, and recoverable.
- Used one generic worker pool instead of separate restaurant/courier worker groups to keep the pipeline simple and demoable.
- Added pending-message recovery so workers can reclaim unacknowledged messages after a crash.
- Used simple bounded in-worker retries; a production version would likely use durable scheduled retries.
- Failed orders are terminal in this version; a production version could add dead-letter replay or operator retry.
- Used representative restaurant/courier simulators with global health settings; a production version could support per-restaurant or per-region failure profiles.
- Did not add caching because order correctness matters more than read optimization for this scope.
- Did not add an API gateway because there is one public API and the main scaling point is the worker layer.

## Additional Documentation

Detailed notes are in `docs/`:

- `docs/01_requirements_functional_nonfunctional.md`
- `docs/02_core_entities.md`
- `docs/03_high_level_design.md`
- `docs/04_demo.md`
- `docs/05_design_choices.md`
- `docs/06_trade_offs.md`
- `docs/07_potential_production_version.md`
