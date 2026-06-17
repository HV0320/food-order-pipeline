# 4. Demo Script

## Start the system

```bash
cp .env.example .env
docker compose up --build -d
docker compose ps
```

Open:

```text
React dashboard: http://localhost:5173
Locust:          http://localhost:8089
API docs:        http://localhost:8000/docs
Restaurant docs: http://localhost:8001/docs
Courier docs:    http://localhost:8002/docs
Grafana:         http://localhost:3001
```

---

## Optional clean reset before demo

Use only when you intentionally want to delete old data:

```bash
docker compose down -v
docker compose up --build -d
```

This clears:

```text
PostgreSQL data
Valkey stream data
Grafana volume data
```

The React dashboard is code-based and comes back automatically.

---

## Demo 1: Show normal pipeline

In React dashboard:

```text
Click All Restaurants Healthy
Click Courier Network Healthy
Click Create 1 Demo Order
```

Show:

- lifecycle funnel
- recent events
- recent orders
- delivered count
- estimated technical backlog returning near zero

Expected lifecycle:

```text
PLACED → CONFIRMED → PREPARING → READY → OUT_FOR_DELIVERY → DELIVERED
```

---

## Demo 2: Mini burst from UI

In React dashboard:

```text
Click Mini Burst: 25 Orders
```

Show:

- total orders increases
- active orders rises briefly
- recent events update
- top restaurants populate
- delivered count increases

---

## Demo 3: Dinner rush with Locust

Open Locust.

Normal traffic:

```text
Users: 10
Spawn rate: 2
Host: http://api:8000
```

Dinner rush:

```text
Users: 50
Spawn rate: 10
Host: http://api:8000
```

Stronger spike:

```text
Users: 100
Spawn rate: 20
Host: http://api:8000
```

Show dashboard:

- created/min rises
- active orders rises
- lifecycle funnel spreads across statuses
- top restaurants populate
- delivered/min changes

---

## Demo 4: Degrade restaurant

With load running, click:

```text
All Restaurants Degraded
```

Show:

- restaurant mode becomes degraded
- restaurant failure/timeout config changes
- failures increase
- recent failures populate
- dead-letter count may increase
- delivered/min may drop

Recover:

```text
All Restaurants Healthy
```

Explain:

```text
Already FAILED orders stay terminal in this simplified version.
New orders should recover and deliver normally.
```

---

## Demo 5: Degrade courier

Click:

```text
Courier Network Degraded
```

Show:

- courier mode becomes degraded
- READY may grow
- OUT_FOR_DELIVERY may fluctuate
- failed orders may increase
- courier-related failures appear

Recover:

```text
Courier Network Healthy
```

---

## Demo 6: Cancellation

To make a cancellable order visible, stop workers first:

```bash
docker compose stop worker
```

In React dashboard:

```text
Click Create 1 Demo Order
```

The order should remain `PLACED`.

In Recent Orders:

```text
Click Cancel
```

Expected:

```text
status = CANCELLED
ORDER_CANCELLED appears in recent events
Cancelled count increases
```

Restart workers:

```bash
docker compose start worker
```

The cancelled order should remain `CANCELLED`.

Explain:

```text
Any old queued workflow message becomes stale and is acknowledged without changing the order.
```

---

## Demo 7: Stop/kill workers

With load running:

```bash
docker compose kill worker
```

Show dashboard:

- active orders increase
- delivered/min slows or stops
- technical backlog or pending messages may increase
- orders remain visible in PostgreSQL

Restart and scale workers:

```bash
docker compose up -d --scale worker=3
```

Show:

- backlog drains
- delivered count rises
- recent events resume

Scale back:

```bash
docker compose up -d --scale worker=1
```

---

## Demo 8: Worker scaling

Start with one worker:

```bash
docker compose up -d --scale worker=1
```

Run load through Locust.

Then scale:

```bash
docker compose up -d --scale worker=3
```

Show:

- active orders drain faster
- delivered/min increases
- estimated technical backlog reduces
- recent events move faster

---

## Useful health commands

```bash
docker compose ps
docker compose logs --tail=100 worker
docker compose logs --tail=100 outbox-relay
docker compose logs --tail=100 api
docker compose exec valkey valkey-cli XINFO GROUPS order.workflow
docker compose exec valkey valkey-cli XLEN order.workflow
docker compose exec valkey valkey-cli XLEN order.dead_letter
docker compose exec valkey valkey-cli XRANGE order.dead_letter - +
```

---

## Smoke test

If `scripts/smoke-test.sh` exists:

```bash
./scripts/smoke-test.sh
```
