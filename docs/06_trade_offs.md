# 6. Trade-offs

## Core Pipeline vs Whole Platform

### Chosen

Build the core order pipeline.

### Not chosen

Build a full marketplace with discovery, matching, payment, and separate operational systems.

### Reason

The assignment focuses on the order pipeline and failure behavior under load. The time box is short, so the system focuses on the most relevant backend and operational concerns.

---

## Valkey Streams vs Celery/RQ/Kafka

### Chosen

Valkey Streams with consumer groups.

### Alternatives

- Celery/RQ
- Kafka

### Reason

Valkey Streams are lightweight, easy to run in Docker Compose, support consumer groups, and make event processing mechanics visible.

Kafka would be more production-like at large scale but too heavy for the time box.

Celery would work but hides some workflow behavior behind framework abstractions.

---

## Single Generic Worker Group vs Separate Worker Groups

### Chosen

One stream and one generic worker consumer group:

```text
order.workflow
order-workers
```

### Alternative

Separate streams/worker groups:

```text
order.restaurant → restaurant-workers
order.courier    → courier-workers
```

### Reason

The single generic worker group keeps the pipeline simpler and demoable.

It still supports horizontal scaling:

```bash
docker compose up -d --scale worker=3
```

### Cost

Restaurant and courier stages cannot be scaled independently in this version.

### Production improvement

Split into stage-specific streams and worker pools if restaurant and courier workloads need independent scaling.

---

## Global Simulator Health vs Per-Restaurant Health

### Chosen

One representative restaurant simulator and one courier simulator.

### Alternative

Per-restaurant failure profiles or multiple restaurant simulator instances.

### Reason

The exercise asks for simulated flaky downstream systems, not a full restaurant marketplace simulation. The current design keeps the focus on pipeline behavior.

### Cost

Changing restaurant health affects all virtual restaurants.

### Mitigation

Orders still include many `restaurant_id` values, and the dashboard shows restaurant-level impact.

---

## Simple In-Worker Retry vs Durable Scheduled Retry

### Chosen

Retry inside the worker process.

### Alternative

Durable scheduled retries using delayed outbox events.

### Reason

The simple retry model is easy to implement, demo, and explain.

### Cost

A worker is occupied while sleeping between retries.

### Production improvement

Store retry attempts durably with `available_at` timestamps and publish retry events later.

---

## Failed Orders Are Terminal

### Chosen

`FAILED` is terminal.

### Alternative

Automatic retry after downstream recovery or dead-letter replay.

### Reason

Terminal failure keeps the first version simple and visible.

### Cost

Old failed orders do not automatically recover.

### Production improvement

Add manual dead-letter replay, compensation flows, or operator-driven retry.

---

## Cancellation Before Dispatch Only

### Chosen

Cancellation allowed before `OUT_FOR_DELIVERY`.

### Alternative

Cancellation at any stage with compensation/refund logic.

### Reason

After dispatch, cancellation becomes more complex because courier/restaurant side effects may already exist.

### Cost

No late cancellation flow.

### Production improvement

Add compensation workflows and downstream cancellation APIs.

---

## SSE vs WebSockets

### Chosen

SSE for dashboard updates.

### Alternative

WebSockets.

### Reason

Dashboard updates are server-to-browser. Controls use normal HTTP POST.

### Cost

SSE is not ideal for high-frequency bidirectional apps.

### Production improvement

Use WebSockets if interactive bidirectional real-time collaboration/control becomes necessary.

---

## No Cache

### Chosen

No cache for order state.

### Alternative

Cache dashboard aggregates or current order status in Valkey.

### Reason

Correctness and simplicity matter more than read optimization.

### Cost

PostgreSQL handles dashboard queries directly.

### Production improvement

Use materialized views, precomputed aggregates, or read models if dashboard load becomes high.

---

## No API Gateway

### Chosen

Expose API directly through Docker Compose.

### Alternative

Nginx/Traefik/API gateway.

### Reason

The system has one public API and the main scaling concern is workers.

### Cost

No gateway-level auth/rate limiting/TLS/routing.

### Production improvement

Add gateway/load balancer in front of API replicas.

---

## React Dashboard vs Grafana/Metabase

### Chosen

React + Vite primary dashboard.

### Alternatives

Grafana or Metabase.

### Reason

The assignment asks for full-stack/web UI, and React allows live controls plus a custom business dashboard.

### Cost

More frontend code than Grafana/Metabase.

### Mitigation

Keep React simple: one page, SSE stream, tables/cards/buttons.

---

## At-Least-Once Instead of Exactly-Once

### Chosen

At-least-once processing with idempotent handlers.

### Alternative

Try to claim exactly-once processing.

### Reason

True end-to-end exactly-once across queues, database, and downstream side effects is unrealistic.

### Cost

Messages may be processed more than once.

### Mitigation

Guarded DB transitions make duplicate messages harmless inside the order pipeline.
