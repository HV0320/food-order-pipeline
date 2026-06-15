import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

const STATUSES = [
  "PLACED",
  "CONFIRMED",
  "PREPARING",
  "READY",
  "OUT_FOR_DELIVERY",
  "DELIVERED",
  "CANCELLED",
  "FAILED"
];

const MENU_ITEMS = [
  ["Burger", 9.99],
  ["Pizza", 13.99],
  ["Chicken Wrap", 11.99],
  ["Fries", 3.99],
  ["Salad", 8.99],
  ["Noodles", 10.99],
  ["Rice Bowl", 12.5],
  ["Tacos", 9.5]
];

function randomItem() {
  const [name, price] = MENU_ITEMS[Math.floor(Math.random() * MENU_ITEMS.length)];
  return {
    name,
    quantity: Math.floor(Math.random() * 2) + 1,
    price
  };
}

function createOrderPayload(prefix = "ui") {
  const id = `${prefix}-${crypto.randomUUID()}`;

  return {
    client_order_id: id,
    customer_id: `customer-${Math.floor(Math.random() * 5000) + 1}`,
    restaurant_id: `restaurant-${Math.floor(Math.random() * 50) + 1}`,
    items: Array.from({ length: Math.floor(Math.random() * 3) + 1 }, randomItem),
    delivery_address: {
      line1: `${Math.floor(Math.random() * 999) + 1} Demo Street`,
      city: "Demo City",
      postcode: "00000"
    }
  };
}

function Card({ title, value, hint, danger }) {
  return (
    <div className={`card ${danger ? "danger" : ""}`}>
      <div className="cardTitle">{title}</div>
      <div className="cardValue">{value ?? "-"}</div>
      {hint ? <div className="hint">{hint}</div> : null}
    </div>
  );
}

function StatusFunnel({ counts }) {
  const max = Math.max(1, ...Object.values(counts || {}));

  return (
    <div className="panel">
      <h2>Lifecycle Funnel</h2>
      {STATUSES.map((status) => {
        const count = counts?.[status] || 0;
        const width = Math.round((count / max) * 100);

        return (
          <div className="barRow" key={status}>
            <div className={`statusLabel ${status === "FAILED" ? "badText" : ""}`}>
              {status}
            </div>
            <div className="barBackground">
              <div className="barFill" style={{ width: `${width}%` }} />
            </div>
            <div className="barCount">{count}</div>
          </div>
        );
      })}
    </div>
  );
}

function Table({ title, columns, rows, emptyText = "No rows yet." }) {
  return (
    <div className="panel">
      <h2>{title}</h2>
      <div className="tableWrap">
        <table>
          <thead>
            <tr>
              {columns.map((column) => (
                <th key={column.key}>{column.label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows && rows.length > 0 ? (
              rows.map((row, index) => (
                <tr key={index}>
                  {columns.map((column) => (
                    <td key={column.key}>{column.render ? column.render(row) : row[column.key]}</td>
                  ))}
                </tr>
              ))
            ) : (
              <tr>
                <td colSpan={columns.length} className="empty">
                  {emptyText}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function shortTime(value) {
  if (!value) return "-";
  return new Date(value).toLocaleTimeString();
}

function formatPercent(value) {
  if (value === undefined || value === null) return "-";
  return `${Math.round(Number(value) * 100)}%`;
}

function formatLatency(config) {
  if (!config) return "-";
  return `${config.min_latency_ms ?? "-"}-${config.max_latency_ms ?? "-"} ms`;
}

function downstreamMode(service, type) {
  const config = service?.config;

  if (!config) return "unknown";

  const failureRate = Number(config.failure_rate ?? 0);
  const timeoutRate = Number(config.timeout_rate ?? 0);
  const noCourierRate = Number(config.no_courier_available_rate ?? 0);

  if (failureRate >= 0.95 || noCourierRate >= 0.95) {
    return "down-like";
  }

  if (failureRate > 0 || timeoutRate > 0 || noCourierRate > 0) {
    return "degraded";
  }

  return "healthy";
}

function DownstreamStatus({ title, service, type }) {
  const config = service?.config || {};
  const counters = service?.counters || {};
  const mode = downstreamMode(service, type);

  return (
    <div className="downstreamCard">
      <div className="downstreamHeader">
        <strong>{title}</strong>
        <span className={`modeBadge ${mode}`}>{mode}</span>
      </div>

      <div className="downstreamGrid">
        <div>
          <span className="smallLabel">Latency</span>
          <strong>{formatLatency(config)}</strong>
        </div>
        <div>
          <span className="smallLabel">Failure</span>
          <strong>{formatPercent(config.failure_rate)}</strong>
        </div>
        <div>
          <span className="smallLabel">Timeout</span>
          <strong>{formatPercent(config.timeout_rate)}</strong>
        </div>
        <div>
          <span className="smallLabel">Rate Limit/sec</span>
          <strong>{config.rate_limit_per_second ?? "-"}</strong>
        </div>
        {type === "courier" ? (
          <div>
            <span className="smallLabel">No Courier</span>
            <strong>{formatPercent(config.no_courier_available_rate)}</strong>
          </div>
        ) : null}
      </div>

      <div className="counterLine">
        requests: {counters.requests_total ?? "-"} |
        success: {counters.success_total ?? "-"} |
        failures: {counters.failures_total ?? "-"} |
        timeouts: {counters.timeouts_total ?? "-"} |
        rate-limited: {counters.rate_limited_total ?? "-"}
      </div>
    </div>
  );
}

function App() {
  const [data, setData] = useState(null);
  const [connection, setConnection] = useState("connecting");
  const [actionMessage, setActionMessage] = useState("");

  useEffect(() => {
    const source = new EventSource("/api/dashboard/stream");

    source.onopen = () => setConnection("connected");

    source.onmessage = (event) => {
      setData(JSON.parse(event.data));
      setConnection("connected");
    };

    source.onerror = () => setConnection("reconnecting");

    return () => source.close();
  }, []);

  async function postControl(path, label) {
    setActionMessage(`Running: ${label}`);

    const response = await fetch(`/api${path}`, {
      method: "POST"
    });

    if (!response.ok) {
      const text = await response.text();
      setActionMessage(`Failed: ${label} - ${text}`);
      return;
    }

    setActionMessage(`Done: ${label}`);
  }

  async function createDemoOrder() {
    const payload = createOrderPayload("ui-demo");

    const response = await fetch("/api/orders", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": payload.client_order_id
      },
      body: JSON.stringify(payload)
    });

    if (!response.ok) {
      setActionMessage(`Failed to create demo order: ${await response.text()}`);
      return;
    }

    setActionMessage("Created 1 demo order");
  }

  async function createMiniBurst(count = 25) {
    setActionMessage(`Creating mini burst of ${count} orders...`);

    const payloads = Array.from({ length: count }, () => createOrderPayload("ui-burst"));

    for (const payload of payloads) {
      await fetch("/api/orders", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": payload.client_order_id
        },
        body: JSON.stringify(payload)
      });
    }

    setActionMessage(`Created mini burst of ${count} orders`);
  }

  const summary = data?.summary || {};
  const queue = data?.queue || {};
  const downstream = data?.downstream || {};
  const estimatedPipelineBacklog =
    queue.estimated_pipeline_backlog ??
    Number(queue.unpublished_outbox_events || 0) +
      Number(queue.consumer_group_lag || 0) +
      Number(queue.pending_messages || 0);

  return (
    <div className="page">
      <header>
        <div>
          <h1>Food Order Pipeline</h1>
          <p>
            Live operations dashboard for order flow, downstream failures, worker recovery, and restaurant-level metrics.
          </p>
        </div>
        <div className={`connection ${connection}`}>
          {connection === "connected" ? "Live SSE connected" : "SSE reconnecting..."}
        </div>
      </header>

      <section className="controls">
        <button onClick={createDemoOrder}>Create 1 Demo Order</button>
        <button onClick={() => createMiniBurst(25)}>Mini Burst: 25 Orders</button>
        <button onClick={() => createMiniBurst(100)}>Mini Burst: 100 Orders</button>

        <button onClick={() => postControl("/control/restaurant/healthy", "restaurant healthy")}>
          All Restaurants Healthy
        </button>
        <button onClick={() => postControl("/control/restaurant/degraded", "restaurant degraded")}>
          All Restaurants Degraded
        </button>
        <button className="dangerButton" onClick={() => postControl("/control/restaurant/down", "restaurant down-like")}>
          All Restaurants Down-like
        </button>

        <button onClick={() => postControl("/control/courier/healthy", "courier healthy")}>
          Courier Network Healthy
        </button>
        <button onClick={() => postControl("/control/courier/degraded", "courier degraded")}>
          Courier Network Degraded
        </button>
        <button className="dangerButton" onClick={() => postControl("/control/courier/down", "courier down-like")}>
          Courier Network Down-like
        </button>
      </section>

      <div className="actionMessage">{actionMessage}</div>

      <section className="grid">
        <Card title="Total Orders" value={summary.total_orders} />
        <Card title="Active Orders" value={summary.active_orders} />
        <Card title="Delivered" value={summary.delivered_orders} />
        <Card title="Failed" value={summary.failed_orders} danger={summary.failed_orders > 0} />
        <Card title="Created / Min" value={summary.orders_created_last_minute} />
        <Card title="Delivered / Min" value={summary.orders_delivered_last_minute} />
        <Card title="Avg Delivery Seconds" value={summary.avg_delivery_seconds} />
        <Card title="Duplicate Client IDs" value={summary.duplicate_client_order_ids} danger={summary.duplicate_client_order_ids > 0} />
        <Card
          title="Estimated Pipeline Backlog"
          value={estimatedPipelineBacklog}
          hint="Outbox + lag + pending"
          danger={estimatedPipelineBacklog > 0}
        />
        <Card title="Unpublished Outbox" value={queue.unpublished_outbox_events} />
        <Card title="Pending Stream Messages" value={queue.pending_messages} />
        <Card title="Consumer Group Lag" value={queue.consumer_group_lag} hint="Estimated unprocessed messages" />
        <Card title="Dead Letter Count" value={queue.dead_letter_count} danger={queue.dead_letter_count > 0} />
        <Card title="Total Workflow Stream Entries" value={queue.workflow_stream_length} hint="Stream history, not current backlog" />
      </section>

      <section className="grid two">
        <div className="panel">
          <h2>Downstream Health</h2>
          <p className="panelNote">
            These controls apply globally to the simulated downstream integration services.
            Orders still belong to many logical restaurants through restaurant_id.
          </p>
          <DownstreamStatus
            title="Restaurant Integration"
            type="restaurant"
            service={downstream.restaurant}
          />
          <DownstreamStatus
            title="Courier Dispatch"
            type="courier"
            service={downstream.courier}
          />
        </div>

        <StatusFunnel counts={data?.status_counts || {}} />
      </section>

      <Table
        title="Top Restaurants"
        rows={data?.restaurant_metrics || []}
        columns={[
          { key: "restaurant_id", label: "Restaurant" },
          { key: "total_orders", label: "Total" },
          { key: "active_orders", label: "Active" },
          { key: "delivered_orders", label: "Delivered" },
          { key: "failed_orders", label: "Failed" },
          { key: "failure_rate_percent", label: "Fail %" }
        ]}
      />

      <Table
        title="Recent Orders"
        rows={data?.recent_orders || []}
        columns={[
          { key: "client_order_id", label: "Client Order" },
          { key: "restaurant_id", label: "Restaurant" },
          { key: "status", label: "Status" },
          { key: "total_amount", label: "Total" },
          { key: "created_at", label: "Created", render: (row) => shortTime(row.created_at) },
          { key: "updated_at", label: "Updated", render: (row) => shortTime(row.updated_at) }
        ]}
      />

      <Table
        title="Stuck / Slow Active Orders"
        rows={data?.stuck_orders || []}
        columns={[
          { key: "client_order_id", label: "Client Order" },
          { key: "restaurant_id", label: "Restaurant" },
          { key: "status", label: "Status" },
          { key: "updated_at", label: "Updated", render: (row) => shortTime(row.updated_at) }
        ]}
      />

      <Table
        title="Recent Failures"
        rows={data?.recent_failures || []}
        columns={[
          { key: "client_order_id", label: "Client Order" },
          { key: "restaurant_id", label: "Restaurant" },
          { key: "status", label: "Status" },
          { key: "failed_reason", label: "Reason" },
          { key: "updated_at", label: "Updated", render: (row) => shortTime(row.updated_at) }
        ]}
      />

      <Table
        title="Recent Events"
        rows={data?.recent_events || []}
        columns={[
          { key: "client_order_id", label: "Client Order" },
          { key: "restaurant_id", label: "Restaurant" },
          { key: "event_type", label: "Event" },
          { key: "from_status", label: "From" },
          { key: "to_status", label: "To" },
          { key: "created_at", label: "Time", render: (row) => shortTime(row.created_at) }
        ]}
      />

      <footer>
        For serious dinner-rush load, use Locust on port 8089. Use terminal commands to stop/start/scale workers.
      </footer>
    </div>
  );
}

createRoot(document.getElementById("root")).render(<App />);
