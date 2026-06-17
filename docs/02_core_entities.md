# 2. Core Entities

## Order

The central business entity.

Represents a customer's food-delivery order.

Lifecycle states:

```text
PLACED
CONFIRMED
PREPARING
READY
OUT_FOR_DELIVERY
DELIVERED
CANCELLED
FAILED
```

---

## Order Item

Represents one item in an order.

---

## Order Event

Append-only audit trail of lifecycle transitions.

Examples:

```text
ORDER_PLACED
ORDER_CONFIRMED
ORDER_PREPARING
ORDER_READY
ORDER_OUT_FOR_DELIVERY
ORDER_DELIVERED
ORDER_FAILED
ORDER_CANCELLED
```

---

## Idempotency Key

Prevents duplicate order creation.

The API uses:

```text
Idempotency-Key
```

or falls back to:

```text
client_order_id
```

If the same key/request is submitted again, the API returns the original order response.

---

## Outbox Event

Durable event record written in PostgreSQL before publishing to Valkey.

Purpose:

```text
Prevent saving an order without also recording workflow work to publish.
```

The outbox relay publishes these records to the Valkey stream.

---

## Workflow Message

A message in Valkey Stream:

```text
order.workflow
```

Example workflow events:

```text
ORDER_PLACED
ORDER_CONFIRMED
ORDER_PREPARING
ORDER_READY
ORDER_OUT_FOR_DELIVERY
```

Each message asks the worker to move the order one lifecycle step forward.

---

## Dead-Letter Message

A message in Valkey Stream:

```text
order.dead_letter
```

Created when the worker cannot process a workflow message after retry exhaustion.

Typical fields:

```text
order_id
event_type
attempts_used
error
```

Dead-letter messages are operational signals. The business order state is still stored in PostgreSQL as `FAILED`.

---

## Worker

Background process that consumes workflow messages and advances orders.

Responsibilities:

- read from Valkey consumer group
- check current order status
- call restaurant/courier simulator
- retry downstream failures
- update PostgreSQL
- write order events
- create next outbox event
- acknowledge stream messages
- recover old pending stream messages
- dead-letter failed messages

---

## Restaurant Simulator

Representative downstream restaurant integration service.

Endpoints include:

```text
GET  /restaurant/health
POST /restaurant/config
POST /restaurant/confirm
POST /restaurant/start-preparation
POST /restaurant/mark-ready
```

Configured with:

```text
min_latency_ms
max_latency_ms
failure_rate
timeout_rate
rate_limit_per_second
```

---

## Courier Simulator

Representative downstream courier dispatch service.

Endpoints include:

```text
GET  /courier/health
POST /courier/config
POST /courier/assign
POST /courier/mark-delivered
```

Configured with:

```text
min_latency_ms
max_latency_ms
failure_rate
timeout_rate
rate_limit_per_second
no_courier_available_rate
```

---

## Dashboard Summary

The dashboard summary is the API response used by the React UI and SSE stream.

It includes:

```text
summary
status_counts
latency
retry
queue
downstream
recent_orders
recent_failures
stuck_orders
restaurant_metrics
recent_events
```

---

## Technical Backlog

The dashboard distinguishes business backlog from technical backlog.

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

This is displayed as:

```text
Estimated Technical Backlog
```
