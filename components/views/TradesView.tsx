"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { Badge, Card, Empty, ErrorBanner, SideBadge, Spinner } from "@/components/ui";
import { dateTime } from "@/lib/format";
import type { TradeIntentStatus } from "@/lib/supabase/types";
import { useTrading } from "@/state/trading";

const ACTIVE = new Set<TradeIntentStatus>([
  "queued",
  "claimed",
  "validating",
  "submitted",
  "open",
  "scaling",
]);

export default function TradesView() {
  const { recentIntents, recentCommands, loading, error, refresh, connections } = useTrading();
  const [activeOnly, setActiveOnly] = useState(false);
  const trades = useMemo(
    () => (activeOnly ? recentIntents.filter((item) => ACTIVE.has(item.status)) : recentIntents),
    [activeOnly, recentIntents],
  );

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Trade queue and history</h1>
          <p>
            Durable Supabase state. Pending instructions survive browser closure and remain visibly
            different from broker-submitted trades.
          </p>
        </div>
        <div className="inline">
          <label className="checkbox" style={{ margin: 0 }}>
            <input
              type="checkbox"
              checked={activeOnly}
              onChange={(event) => setActiveOnly(event.target.checked)}
            />
            <span>Active only</span>
          </label>
          <button className="btn btn-sm" onClick={() => void refresh()} disabled={loading}>
            {loading ? "Refreshing..." : "Refresh"}
          </button>
        </div>
      </div>

      <ErrorBanner error={error} />

      <Card
        title="Instructions"
        actions={
          <span className="inline">
            <Badge tone="muted">{trades.length}</Badge>
            <Badge tone="info">{recentCommands.filter((item) => item.status === "pending").length} pending</Badge>
          </span>
        }
      >
        {loading && recentIntents.length === 0 ? (
          <Spinner label="loading trade instructions" />
        ) : trades.length === 0 ? (
          <Empty>
            No trade instructions yet. <Link href="/trade">Plan one</Link>.
          </Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Created</th>
                  <th>Account</th>
                  <th>Symbol</th>
                  <th>Side</th>
                  <th>Status</th>
                  <th>Stop</th>
                  <th>Execution deadline</th>
                  <th>Broker ticket</th>
                </tr>
              </thead>
              <tbody>
                {trades.map((trade) => {
                  const connection = connections.find((item) => item.id === trade.connection_id);
                  return (
                    <tr key={trade.id}>
                      <td className="small nowrap">{dateTime(trade.created_at)}</td>
                      <td className="small">{connection?.label ?? trade.connection_id.slice(0, 8)}</td>
                      <td className="strong"><Link href={`/trades/${trade.id}`}>{trade.symbol}</Link></td>
                      <td><SideBadge side={trade.side} /></td>
                      <td>
                        <IntentStatus status={trade.status} />
                        {trade.last_error && <div className="tiny neg">{trade.last_error}</div>}
                      </td>
                      <td className="num">
                        {trade.stop_loss !== null
                          ? trade.stop_loss
                          : `${trade.stop_points ?? "-"} points`}
                      </td>
                      <td className="small nowrap">{dateTime(trade.execute_before)}</td>
                      <td className="mono small">
                        {trade.broker_position_ticket ?? trade.broker_order_ticket ?? "-"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <div className="mt">
        <Card title="Why these states matter">
          <p className="small muted mb-0">
            <strong>Queued</strong> is only a saved request. A broker order exists only after the
            worker records <strong>submitted</strong> or <strong>open</strong> with a ticket. Expired,
            rejected, failed, and cancelled rows are retained for discipline and audit history.
          </p>
        </Card>
      </div>
    </>
  );
}

function IntentStatus({ status }: { status: TradeIntentStatus }) {
  const tone =
    status === "open" || status === "submitted" || status === "closed"
      ? "ok"
      : status === "rejected" || status === "failed"
        ? "danger"
        : status === "cancelled" || status === "expired"
          ? "muted"
          : "warn";
  return <Badge tone={tone}>{status}</Badge>;
}
