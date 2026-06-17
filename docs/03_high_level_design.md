# 3. High-Level Design

## Architecture Overview

```text
React Dashboard / Locust
        ↓
FastAPI Order API
        ↓
PostgreSQL
        ↓
outbox_events
        ↓
Outbox Relay
        ↓
Valkey Stream
        ↓
Worker replicas
        ↓
Restaurant Simulator / Courier Simulator
        ↓
PostgreSQL order status + order_events
```

---

## Services

### `api`

FastAPI service.

Responsibilities:

- accept orders
- enforce idempotency
- persist orders
- write order events
- write outbox events
- expose dashboard summary
- stream live dashboard updates through SSE
- proxy simulator control actions
- expose cancellation endpoint

---

### `postgres`

Source of truth.

Stores:

- orders
- order items
- order events
- idempotency keys
- outbox events

---

### `valkey`

Workflow stream service.

Streams:

```text
order.workflow
order.dead_letter
```

Consumer group:

```text
order-workers
```

---

### `outbox-relay`

Publishes durable outbox events to Valkey Stream.

Flow:

```text
outbox_events unpublished row
→ XADD order.workflow
→ mark outbox row as published
```

---

### `worker`

Generic workflow processor.

All worker replicas consume from the same stream and consumer group:

```text
Stream: order.workflow
Group:  order-workers
```

The worker decides which downstream service to call based on `event_type`.

---

### `restaurant-sim`

Simulates flaky restaurant integration.

---

### `courier-sim`

Simulates flaky courier dispatch integration.

---

### `frontend`

React + Vite dashboard.

Uses:

```text
SSE for live updates
HTTP POST for dashboard controls
```

---

### `loadgen`

Locust load generator.

Creates orders across many virtual `restaurant_id` values.

---

## Order Creation Flow

```text
1. Client sends POST /orders.
2. API validates request.
3. API checks idempotency.
4. API inserts order with status PLACED.
5. API inserts order items.
6. API inserts ORDER_PLACED into order_events.
7. API inserts ORDER_PLACED into outbox_events.
8. API returns order response.
```

The API does not call restaurant or courier systems.

---

## Workflow Processing Flow

```text
1. Outbox relay publishes ORDER_PLACED to order.workflow.
2. Worker reads ORDER_PLACED.
3. Worker checks order is PLACED.
4. Worker calls restaurant /confirm.
5. Worker updates order to CONFIRMED.
6. Worker writes ORDER_CONFIRMED event.
7. Worker writes next outbox event ORDER_CONFIRMED.
8. Relay publishes next event.
9. Worker repeats until DELIVERED.
```

---

## Lifecycle Mapping

| Workflow Event | Expected Status | Downstream Call | Next Status |
|---|---|---|---|
| `ORDER_PLACED` | `PLACED` | restaurant `/confirm` | `CONFIRMED` |
| `ORDER_CONFIRMED` | `CONFIRMED` | restaurant `/start-preparation` | `PREPARING` |
| `ORDER_PREPARING` | `PREPARING` | restaurant `/mark-ready` | `READY` |
| `ORDER_READY` | `READY` | courier `/assign` | `OUT_FOR_DELIVERY` |
| `ORDER_OUT_FOR_DELIVERY` | `OUT_FOR_DELIVERY` | courier `/mark-delivered` | `DELIVERED` |

---

## Cancellation Flow

```text
POST /orders/{order_id}/cancel
```

Allowed before dispatch:

```text
PLACED
CONFIRMED
PREPARING
READY
```

Rejected after:

```text
OUT_FOR_DELIVERY
DELIVERED
FAILED
CANCELLED
```

Cancellation writes `ORDER_CANCELLED`.

If an old workflow message later arrives, the worker treats it as stale.

---

## Failure and Dead-Letter Flow

```text
1. Worker calls downstream.
2. Downstream fails.
3. Worker retries up to MAX_ATTEMPTS.
4. If attempts are exhausted:
   - order becomes FAILED
   - failed_reason is stored
   - ORDER_FAILED is written
   - dead-letter message is published to order.dead_letter
   - original workflow message is acknowledged
```

---

## Pending Message Recovery

If a worker crashes before acknowledging a message:

```text
message remains pending in consumer group
```

Live workers periodically:

```text
1. inspect pending messages
2. claim idle messages
3. process them again
4. acknowledge them
```

Reprocessing is safe because transitions are guarded by expected status.

---

## Dashboard Flow

```text
React dashboard opens
        ↓
EventSource connects to /dashboard/stream
        ↓
FastAPI sends dashboard JSON every 2 seconds
        ↓
React updates dashboard without page refresh
```

Dashboard controls use HTTP POST.

---

## Scalability Model

The API accepts orders quickly and enqueues workflow work.

Workers are scaled independently:

```bash
docker compose up -d --scale worker=3
```

The worker pool shares messages through the Valkey consumer group.

---

## Business vs Technical Backlog

Business backlog:

```text
Active Orders
```

Technical backlog:

```text
Unpublished Outbox
+ Consumer Group Lag
+ Pending Stream Messages
```

Technical backlog may include stale/no-op workflow messages such as a cancelled order's old `ORDER_PLACED` message before the worker acknowledges it.
