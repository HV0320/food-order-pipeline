# 1. Requirements

## Functional Requirements

### Order ingestion

The system must accept customer orders through an API.

Required behavior:

- Accept a new order with customer, restaurant, items, and delivery address.
- Assign the order an internal ID.
- Store the order durably.
- Return quickly without waiting for the full delivery lifecycle.
- Prevent duplicate order creation when the client retries the same request.

Implemented with:

- `POST /orders`
- `GET /orders`
- `GET /orders/{order_id}`
- `Idempotency-Key`
- `client_order_id`
- PostgreSQL unique constraints and idempotency records

---

### Order lifecycle

Orders move through a controlled lifecycle.

Primary lifecycle:

```text
PLACED
→ CONFIRMED
→ PREPARING
→ READY
→ OUT_FOR_DELIVERY
→ DELIVERED
```

Terminal states:

```text
DELIVERED
CANCELLED
FAILED
```

The system must prevent invalid transitions such as:

```text
READY → PREPARING
PLACED → OUT_FOR_DELIVERY
DELIVERED → PREPARING
CANCELLED → DELIVERED
```

Implemented with:

- Worker state machine
- PostgreSQL guarded updates
- `order_events` audit trail

---

### Cancellation

The exercise states that orders can be cancelled or fail along the way.

Implemented cancellation rule:

```text
Allowed:
PLACED
CONFIRMED
PREPARING
READY

Rejected:
OUT_FOR_DELIVERY
DELIVERED
FAILED
CANCELLED
```

Endpoint:

```text
POST /orders/{order_id}/cancel
```

Cancellation writes:

```text
ORDER_CANCELLED
```

to `order_events`.

If an old workflow message later tries to process the cancelled order, the worker treats it as stale because the order is no longer in the expected status.

---

### Asynchronous pipeline processing

The API must not perform the full fulfillment workflow synchronously.

Implemented flow:

```text
API
→ PostgreSQL order + outbox event
→ Outbox relay
→ Valkey Stream
→ Worker
→ Restaurant/Courier simulators
→ PostgreSQL status update
```

This lets the API absorb bursts while workers process order lifecycle work asynchronously.

---

### Downstream system simulation

The system must simulate flaky restaurant and courier systems.

Implemented simulators:

```text
restaurant-sim
courier-sim
```

Each simulator supports:

- Configurable latency
- Configurable failure rate
- Configurable timeout rate
- Configurable rate limit
- Health endpoint
- Recovery through configuration update

Representative control modes:

```text
Healthy
Degraded
Down-like
```

---

### Retry and failure handling

Workers retry failed downstream calls before failing an order.

Simplified retry model:

```text
MAX_ATTEMPTS = 3

Attempt 1
→ if failed, wait 1 second

Attempt 2
→ if failed, wait 2 seconds

Attempt 3
→ if failed, mark order FAILED and dead-letter
```

After retry exhaustion:

- `orders.status = FAILED`
- `orders.failed_reason` is set
- `ORDER_FAILED` is written to `order_events`
- a message is written to `order.dead_letter`

---

### Dead-letter handling

Permanently failed workflow messages are published to:

```text
order.dead_letter
```

Dead-letter messages provide technical visibility into workflow messages that failed after retries.

The business source of truth remains PostgreSQL:

```text
orders.status = FAILED
order_events includes ORDER_FAILED
```

---

### Pending message recovery

If a worker receives a Valkey Stream message and crashes before acknowledging it, the message may remain pending.

The worker includes pending-message recovery using `XPENDING` / `XCLAIM`-style logic:

```text
1. Inspect pending messages.
2. Find messages idle longer than a threshold.
3. Claim them for a live worker.
4. Process them again.
5. Acknowledge them.
```

This supports recovery after worker crashes.

---

### Load generation

The project includes a load generator to create realistic order traffic.

Implemented with:

```text
Locust
```

Use cases:

- quiet traffic
- normal dinner traffic
- dinner-rush spike
- promotion-style spike

Locust creates orders across many virtual `restaurant_id` values.

---

### Live dashboard

The project includes a web UI dashboard.

Implemented with:

```text
React + Vite
FastAPI SSE stream
```

Dashboard shows:

- Orders now
- Lifecycle funnel
- Throughput
- Latency
- Stage latency
- Downstream health
- Restaurant-level metrics
- Stuck orders
- Recent failures
- Recent orders
- Recent events
- Technical backlog
- Dead-letter count
- Consumer group lag
- Pending stream messages

---

## Non-Functional Requirements

### Single-machine runtime

The full system runs with Docker Compose on a single Docker host.

Expected reviewer flow:

```bash
git clone <repo>
cd food-order-pipeline
cp .env.example .env
docker compose up --build
```

---

### Reliability

The system should avoid lost and duplicate orders.

Implemented through:

- PostgreSQL source of truth
- API idempotency
- `client_order_id` uniqueness
- outbox pattern
- Valkey Streams
- at-least-once processing
- guarded state transitions
- pending message recovery
- terminal states
- dead-letter stream

---

### Scalability

The system demonstrates scalability at the worker layer.

Workers are stateless replicas that consume from a shared Valkey consumer group.

Scale command:

```bash
docker compose up -d --scale worker=3
```

Expected result:

- higher lifecycle processing throughput
- active orders drain faster
- technical backlog decreases

---

### Business Dashboard

The system provides live visibility through:

- React dashboard
- dashboard SSE stream
- dashboard cards/tables
- recent order events
- failure reasons
- downstream health/config
- queue/backlog indicators
- Docker Compose logs
- Valkey CLI inspection

---

### Demoability

The system must make the required demo scenarios quick to trigger:

- create an order
- create a dinner rush
- degrade restaurant/courier systems
- recover downstream systems
- stop/kill workers
- scale workers
- observe pipeline behavior
- observe failure and recovery
