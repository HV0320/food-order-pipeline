# 5. Design Choices

## Focus on the Core Order Pipeline

The implementation focuses on the order pipeline described in the exercise:

```text
order intake
lifecycle progression
flaky downstream handling
load generation
live visibility
failure/recovery behavior
```

It does not attempt to build the broader marketplace platform.

---

## PostgreSQL as Source of Truth

PostgreSQL stores the authoritative order state.

Reasons:

- order state is business-critical
- workers can crash without losing state
- queues can be rebuilt or replayed from durable data
- dashboard can query current truth
- lifecycle history is auditable

Valkey is not the source of truth.

---

## No Cache for Order State

Valkey is used as a workflow stream, not as a cache.

Caching order state would add:

- stale reads
- cache invalidation
- harder debugging
- possible dashboard inconsistency

Correctness is prioritized over read optimization.

---

## Fast API Ingestion, Async Fulfillment

The API returns after durable persistence.

It does not synchronously call restaurant/courier systems.

This lets the system absorb bursty traffic and process fulfillment asynchronously.

---

## Idempotent Order Creation

Order intake uses:

```text
Idempotency-Key
client_order_id
unique constraints
idempotency_keys table
```

This prevents duplicate order rows from client retries or double submits.

---

## Outbox Pattern

Order creation and event creation are committed together in PostgreSQL.

Then the outbox relay publishes to Valkey.

This prevents:

```text
order saved but workflow event lost
```

The outbox is one of the main reliability mechanisms.

---

## Durable Pub/Sub-Style Workflow with Valkey Streams

The system uses Valkey Streams as a durable pub/sub-style workflow queue.

It does not use classic Redis Pub/Sub because classic Pub/Sub is transient and subscribers can miss messages.

Valkey Streams provide:

- message persistence
- consumer groups
- acknowledgements
- pending entries
- recovery from crashed workers
- worker scaling
- stream inspection
- dead-letter stream

The flow is:

```text
API writes outbox event
→ outbox relay publishes to order.workflow
→ worker consumer group reads message
→ worker processes message
→ worker acknowledges message
```

This is pub/sub-like because producers and consumers are decoupled, but it is safer than fire-and-forget Pub/Sub.

---

## Single Generic Worker Consumer Group

The current design uses:

```text
Stream: order.workflow
Consumer group: order-workers
Worker service: generic workflow worker
```

There are not separate restaurant-worker and courier-worker consumer groups.

Instead, generic workers inspect `event_type` and call the appropriate downstream service.

Examples:

```text
ORDER_PLACED           → restaurant /confirm
ORDER_CONFIRMED        → restaurant /start-preparation
ORDER_PREPARING        → restaurant /mark-ready
ORDER_READY            → courier /assign
ORDER_OUT_FOR_DELIVERY → courier /mark-delivered
```

The lifecycle dependency is enforced by:

- event sequencing
- PostgreSQL current status
- guarded state transitions

This keeps the pipeline simple and easy to scale for the take-home.

A production version could split restaurant and courier stages into separate streams and worker pools.

---

## At-Least-Once Processing + Idempotency

Workers may see the same message more than once.

This is expected and safe.

The worker only updates the order if the current status matches the expected status.

Example:

```sql
UPDATE orders
SET status = 'OUT_FOR_DELIVERY'
WHERE id = :order_id
  AND status = 'READY';
```

If a duplicate message arrives after the order moved forward, the update affects zero rows and the message is treated as stale.

The system does not claim fake exactly-once processing.

---

## Pending Message Recovery

If a worker receives a message but crashes before `XACK`, the message remains pending in the Valkey consumer group.

The worker includes `XPENDING` / `XCLAIM`-style recovery:

```text
inspect pending messages
claim old idle messages
process again
acknowledge
```

Reprocessing is safe because of guarded state transitions.

---

## Simple Bounded Retries

The worker uses simple retries inside the worker process.

Default:

```text
MAX_ATTEMPTS = 3
```

Retry delays:

```text
attempt 1 fails → wait 1 second
attempt 2 fails → wait 2 seconds
attempt 3 fails → mark FAILED
```

This is intentionally simple for the time-boxed version.

---

## Dead-Letter Stream

When retries are exhausted:

```text
order becomes FAILED
ORDER_FAILED event is written
dead-letter message is published to order.dead_letter
original workflow message is acknowledged
```

Dead-letter stream gives operational visibility into permanently failed workflow messages.

---

## React + Vite Dashboard

The primary demo UI is a React + Vite dashboard.

Reasons:

- clearly satisfies full-stack/web UI expectation
- provides live operations view
- includes demo control buttons
- easier to follow than terminal-only or Grafana-only demo
- cloneable as part of the repo

---

## SSE Instead of WebSockets

The dashboard uses Server-Sent Events for live updates.

Reason:

```text
The dashboard mostly needs server → browser updates.
Controls can use normal HTTP POST.
```

SSE is simpler than WebSockets for this use case.

---

## Locust for Serious Load

The React dashboard includes mini-burst buttons, but serious dinner-rush load is generated with Locust.

Reason:

- load can be dialed up/down
- users and spawn rate are visible
- better for burst demos
- avoids making the dashboard responsible for heavy load

---

## Worker Scaling with Docker Compose

Worker scaling is infrastructure-level.

Command:

```bash
docker compose up -d --scale worker=3
```

React shows the effect but does not control Docker.

This avoids exposing Docker control through the application.

---

## No API Gateway in This Version

The system exposes one public API.

The main scalability concern is async workflow processing, not HTTP routing.

A production version could add:

- API gateway
- auth
- TLS termination
- API rate limiting
- load balancing across API replicas

---

## Business Backlog vs Technical Backlog

The dashboard distinguishes:

```text
Active Orders = business backlog
Estimated Technical Backlog = internal outbox/stream/pending work
```

Technical backlog can include stale/no-op messages such as old workflow messages for cancelled orders.

This distinction makes failure/recovery behavior easier to explain.
