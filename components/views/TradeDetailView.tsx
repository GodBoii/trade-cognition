"use client";

import Link from "next/link";
import { useState } from "react";

import { Badge, Banner, Card, ErrorBanner, PageHead, SideBadge, Spinner } from "@/components/ui";
import {
  enqueueTradeCommand,
  getTradeIntent,
  listTradeEventsForIntent,
} from "@/lib/supabase/data";
import { dateTime } from "@/lib/format";
import { useAsync } from "@/lib/useAsync";
import { useAuth } from "@/state/auth";
import { useTrading } from "@/state/trading";

export default function TradeDetailView({ tradeId }: { tradeId: string }) {
  const { user } = useAuth();
  const { refresh } = useTrading();
  const loaded = useAsync(async () => {
    if (!user) throw new Error("Sign in to view this trade.");
    const [trade, events] = await Promise.all([
      getTradeIntent(user.id, tradeId),
      listTradeEventsForIntent(user.id, tradeId),
    ]);
    return { trade, events };
  }, [user?.id, tradeId]);
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [note, setNote] = useState<string | null>(null);

  if (loaded.loading) return <Spinner label="loading trade from Supabase" />;
  if (loaded.error) return <ErrorBanner error={loaded.error} />;
  if (!loaded.data) return null;
  const { trade, events } = loaded.data;
  const active = ["submitted", "open", "scaling"].includes(trade.status);

  const queue = async (kind: "close_trade" | "sync_trade") => {
    setBusy(kind);
    setActionError(null);
    setNote(null);
    try {
      const command = await enqueueTradeCommand({
        connectionId: trade.connection_id,
        intentId: trade.id,
        commandType: kind,
        clientRequestId: crypto.randomUUID(),
      });
      setNote(`${kind.replace(/_/g, " ")} queued as ${command.id.slice(0, 8)}.`);
      await Promise.all([loaded.reload(), refresh()]);
    } catch (cause) {
      setActionError(cause);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="page-enter">
      <PageHead
        title={trade.symbol}
        subtitle={`Intent ${trade.id} · created ${dateTime(trade.created_at)}`}
        actions={
          <>
            <SideBadge side={trade.side} />
            <Badge tone="info">{trade.status}</Badge>
            <Link href="/trades" className="btn btn-sm">back to list</Link>
            <button className="btn btn-sm" disabled={busy !== null} onClick={() => void queue("sync_trade")}>
              {busy === "sync_trade" ? "Queuing..." : "Queue broker sync"}
            </button>
            {active && (
              <button className="btn btn-sm btn-danger" disabled={busy !== null} onClick={() => void queue("close_trade")}>
                {busy === "close_trade" ? "Queuing..." : "Queue close"}
              </button>
            )}
          </>
        }
      />

      <ErrorBanner error={actionError} />
      {note && <Banner tone="ok">{note} Closing this browser will not remove the command.</Banner>}

      <div className="grid grid-2">
        <Card title="Instruction">
          <dl className="kv">
            <dt>Side / order</dt><dd>{trade.side} · {trade.order_kind}</dd>
            <dt>Requested entry</dt><dd>{trade.requested_entry ?? "market quote"}</dd>
            <dt>Stop</dt><dd>{trade.stop_loss ?? `${trade.stop_points} points`}</dd>
            <dt>Requested volume</dt><dd>{trade.requested_volume ?? "strict formula"}</dd>
            <dt>Execution deadline</dt><dd>{dateTime(trade.execute_before)}</dd>
            <dt>Order ticket</dt><dd className="mono">{trade.broker_order_ticket ?? "-"}</dd>
            <dt>Position ticket</dt><dd className="mono">{trade.broker_position_ticket ?? "-"}</dd>
          </dl>
          {trade.last_error && <p className="small neg mb-0">{trade.last_error}</p>}
        </Card>

        <Card title="Worker decision">
          {trade.approved_plan || trade.rules_report ? (
            <>
              <p className="small muted">The worker recorded these snapshots when it validated the order.</p>
              <details>
                <summary>Approved calculation</summary>
                <pre className="tiny">{JSON.stringify(trade.approved_plan, null, 2)}</pre>
              </details>
              <details className="mt">
                <summary>Rule report</summary>
                <pre className="tiny">{JSON.stringify(trade.rules_report, null, 2)}</pre>
              </details>
            </>
          ) : (
            <p className="muted mb-0">
              No decision has been published. Queued is not equivalent to approved or executed.
            </p>
          )}
        </Card>
      </div>

      <div className="mt">
        <Card title="Audit events" actions={<Badge tone="muted">{events.length}</Badge>}>
          {events.length === 0 ? (
            <p className="muted mb-0">No worker events for this instruction yet.</p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead><tr><th>Time</th><th>Event</th><th>Message</th></tr></thead>
                <tbody>
                  {events.map((event) => (
                    <tr key={event.id}>
                      <td className="small nowrap">{dateTime(event.created_at)}</td>
                      <td><Badge tone="info">{event.event_type.replace(/_/g, " ")}</Badge></td>
                      <td className="small">{event.message || "-"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
