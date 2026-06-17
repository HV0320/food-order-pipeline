#!/usr/bin/env bash
set -euo pipefail

API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
RESTAURANT_BASE_URL="${RESTAURANT_BASE_URL:-http://localhost:8001}"
COURIER_BASE_URL="${COURIER_BASE_URL:-http://localhost:8002}"

PYTHON_BIN="${PYTHON_BIN:-python}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN="python3"
fi

log() {
  printf "\n==> %s\n" "$1"
}

fail() {
  printf "\nERROR: %s\n" "$1" >&2
  exit 1
}

wait_for_url() {
  local url="$1"
  local label="$2"

  for _ in $(seq 1 60); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      log "$label is ready"
      return 0
    fi

    sleep 1
  done

  fail "Timed out waiting for $label at $url"
}

json_get() {
  local key="$1"
  "$PYTHON_BIN" -c "import sys,json; print(json.load(sys.stdin)['$key'])"
}

get_order_status() {
  local order_id="$1"

  curl -fsS "$API_BASE_URL/orders/$order_id" \
    | "$PYTHON_BIN" -c 'import sys,json; print(json.load(sys.stdin)["status"])'
}

set_downstreams_healthy() {
  log "Setting restaurant simulator to healthy"

  curl -fsS -X POST "$RESTAURANT_BASE_URL/restaurant/config" \
    -H "Content-Type: application/json" \
    -d '{
      "min_latency_ms": 100,
      "max_latency_ms": 300,
      "failure_rate": 0,
      "timeout_rate": 0,
      "rate_limit_per_second": 100
    }' >/dev/null

  log "Setting courier simulator to healthy"

  curl -fsS -X POST "$COURIER_BASE_URL/courier/config" \
    -H "Content-Type: application/json" \
    -d '{
      "min_latency_ms": 100,
      "max_latency_ms": 300,
      "failure_rate": 0,
      "timeout_rate": 0,
      "rate_limit_per_second": 100,
      "no_courier_available_rate": 0
    }' >/dev/null
}

create_order() {
  local client_order_id="$1"
  local file_path="$2"

  curl -fsS -X POST "$API_BASE_URL/orders" \
    -H "Content-Type: application/json" \
    -H "Idempotency-Key: $client_order_id" \
    -d @"$file_path"
}

log "Waiting for services"
wait_for_url "$API_BASE_URL/health/live" "API live endpoint"
wait_for_url "$API_BASE_URL/health/ready" "API readiness endpoint"
wait_for_url "$RESTAURANT_BASE_URL/restaurant/health" "restaurant simulator"
wait_for_url "$COURIER_BASE_URL/courier/health" "courier simulator"

set_downstreams_healthy

log "Checking dashboard summary shape"

SUMMARY_JSON="$(curl -fsS "$API_BASE_URL/dashboard/summary")"

printf "%s" "$SUMMARY_JSON" | "$PYTHON_BIN" -c '
import sys, json

data = json.load(sys.stdin)

required_top = [
    "summary",
    "status_counts",
    "queue",
    "downstream",
    "recent_orders",
    "recent_failures",
    "stuck_orders",
    "restaurant_metrics",
    "recent_events",
]

missing = [key for key in required_top if key not in data]
if missing:
    raise SystemExit(f"Missing dashboard keys: {missing}")

required_queue = [
    "workflow_stream_length",
    "dead_letter_count",
    "pending_messages",
    "consumer_group_lag",
    "unpublished_outbox_events",
    "estimated_pipeline_backlog",
]

missing_queue = [key for key in required_queue if key not in data["queue"]]
if missing_queue:
    raise SystemExit(f"Missing queue keys: {missing_queue}")

print("Dashboard summary shape OK")
'

log "Creating an order and checking idempotency"

TEST_ID="smoke-order-$(date +%s)-$RANDOM"

cat > /tmp/smoke-order.json <<JSON
{
  "client_order_id": "$TEST_ID",
  "customer_id": "smoke-customer",
  "restaurant_id": "restaurant-smoke",
  "items": [
    {
      "name": "Smoke Test Pizza",
      "quantity": 1,
      "price": 10.99
    }
  ],
  "delivery_address": {
    "line1": "123 Smoke Test Street",
    "city": "Demo City",
    "postcode": "00000"
  }
}
JSON

CREATE_RESPONSE="$(create_order "$TEST_ID" /tmp/smoke-order.json)"
ORDER_ID="$(printf "%s" "$CREATE_RESPONSE" | json_get "order_id")"

DUPLICATE_RESPONSE="$(create_order "$TEST_ID" /tmp/smoke-order.json)"
DUPLICATE_ORDER_ID="$(printf "%s" "$DUPLICATE_RESPONSE" | json_get "order_id")"

if [ "$ORDER_ID" != "$DUPLICATE_ORDER_ID" ]; then
  fail "Idempotency failed. First order_id=$ORDER_ID duplicate order_id=$DUPLICATE_ORDER_ID"
fi

log "Idempotency OK. order_id=$ORDER_ID"

log "Waiting for order to reach DELIVERED"

FINAL_STATUS=""

for _ in $(seq 1 60); do
  FINAL_STATUS="$(get_order_status "$ORDER_ID")"

  if [ "$FINAL_STATUS" = "DELIVERED" ]; then
    break
  fi

  sleep 1
done

if [ "$FINAL_STATUS" != "DELIVERED" ]; then
  curl -fsS "$API_BASE_URL/orders/$ORDER_ID" | "$PYTHON_BIN" -m json.tool || true
  fail "Expected order $ORDER_ID to become DELIVERED, got $FINAL_STATUS"
fi

log "Order delivered successfully"

log "Checking lifecycle events for delivered order"

EVENTS_JSON="$(curl -fsS "$API_BASE_URL/orders/$ORDER_ID/events")"

printf "%s" "$EVENTS_JSON" | "$PYTHON_BIN" -c '
import sys, json

events = [item["event_type"] for item in json.load(sys.stdin)]
required = [
    "ORDER_PLACED",
    "ORDER_CONFIRMED",
    "ORDER_PREPARING",
    "ORDER_READY",
    "ORDER_OUT_FOR_DELIVERY",
    "ORDER_DELIVERED",
]

missing = [event for event in required if event not in events]

if missing:
    raise SystemExit(f"Missing expected lifecycle events: {missing}. Actual events: {events}")

print("Lifecycle events OK")
'

log "Testing cancellation before worker processing"

cleanup() {
  docker compose start worker >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker compose stop worker >/dev/null

CANCEL_TEST_ID="smoke-cancel-order-$(date +%s)-$RANDOM"

cat > /tmp/smoke-cancel-order.json <<JSON
{
  "client_order_id": "$CANCEL_TEST_ID",
  "customer_id": "smoke-cancel-customer",
  "restaurant_id": "restaurant-smoke-cancel",
  "items": [
    {
      "name": "Cancel Test Pizza",
      "quantity": 1,
      "price": 12.99
    }
  ],
  "delivery_address": {
    "line1": "456 Cancel Street",
    "city": "Demo City",
    "postcode": "00000"
  }
}
JSON

CANCEL_CREATE_RESPONSE="$(create_order "$CANCEL_TEST_ID" /tmp/smoke-cancel-order.json)"
CANCEL_ORDER_ID="$(printf "%s" "$CANCEL_CREATE_RESPONSE" | json_get "order_id")"

curl -fsS -X POST "$API_BASE_URL/orders/$CANCEL_ORDER_ID/cancel" \
  -H "Content-Type: application/json" \
  -d '{"reason":"smoke_test_cancel"}' >/dev/null

CANCEL_STATUS="$(get_order_status "$CANCEL_ORDER_ID")"

if [ "$CANCEL_STATUS" != "CANCELLED" ]; then
  fail "Expected cancelled order to be CANCELLED, got $CANCEL_STATUS"
fi

log "Cancellation endpoint OK"

docker compose start worker >/dev/null

sleep 8

CANCEL_STATUS_AFTER_WORKER="$(get_order_status "$CANCEL_ORDER_ID")"

if [ "$CANCEL_STATUS_AFTER_WORKER" != "CANCELLED" ]; then
  fail "Cancelled order moved after worker restart. Expected CANCELLED, got $CANCEL_STATUS_AFTER_WORKER"
fi

log "Cancelled order stayed CANCELLED after worker restart"

CANCEL_EVENTS_JSON="$(curl -fsS "$API_BASE_URL/orders/$CANCEL_ORDER_ID/events")"

printf "%s" "$CANCEL_EVENTS_JSON" | "$PYTHON_BIN" -c '
import sys, json

events = [item["event_type"] for item in json.load(sys.stdin)]

if "ORDER_PLACED" not in events:
    raise SystemExit(f"ORDER_PLACED missing from cancelled order events: {events}")

if "ORDER_CANCELLED" not in events:
    raise SystemExit(f"ORDER_CANCELLED missing from cancelled order events: {events}")

print("Cancellation events OK")
'

trap - EXIT

log "Final dashboard summary check"

curl -fsS "$API_BASE_URL/dashboard/summary" \
  | "$PYTHON_BIN" -m json.tool >/dev/null

log "Smoke test passed"
