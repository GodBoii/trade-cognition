"use client";

import { Badge, Card, Empty, ErrorBanner, Spinner } from "@/components/ui";
import { dateTime } from "@/lib/format";
import { useTrading } from "@/state/trading";

export default function JournalView() {
  const { recentEvents, recentIntents, loading, error, refresh } = useTrading();
  const rejected = recentIntents.filter((item) => item.status === "rejected").length;
  const expired = recentIntents.filter((item) => item.status === "expired").length;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Execution journal</h1>
          <p>
            Append-only worker events and durable decisions from Supabase. Rejections and expiry are
            recorded outcomes, not hidden transport errors.
          </p>
        </div>
        <button className="btn btn-sm" onClick={() => void refresh()} disabled={loading}>
          {loading ? "Refreshing..." : "Refresh"}
        </button>
      </div>

      <ErrorBanner error={error} />

      <div className="grid grid-3">
        <Card title="Events"><div className="stat-value">{recentEvents.length}</div></Card>
        <Card title="Rule rejections"><div className="stat-value">{rejected}</div></Card>
        <Card title="Expired instructions"><div className="stat-value">{expired}</div></Card>
      </div>

      <div className="mt">
        <Card title="Activity" actions={<Badge tone="muted">latest {recentEvents.length}</Badge>}>
          {loading && recentEvents.length === 0 ? (
            <Spinner label="loading audit events" />
          ) : recentEvents.length === 0 ? (
            <Empty>
              No worker events yet. Pair a worker and submit a trade instruction to begin the audit
              trail.
            </Empty>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr><th>Time</th><th>Event</th><th>Message</th><th>Intent</th></tr>
                </thead>
                <tbody>
                  {recentEvents.map((event) => (
                    <tr key={event.id}>
                      <td className="small nowrap">{dateTime(event.created_at)}</td>
                      <td><EventBadge eventType={event.event_type} /></td>
                      <td className="small">{event.message || "-"}</td>
                      <td className="mono tiny">{event.intent_id?.slice(0, 8) ?? "-"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>

      <div className="mt">
        <Card title="Audit guarantees">
          <ul className="small muted" style={{ margin: 0, paddingLeft: 18 }}>
            <li>The browser cannot insert, edit, or delete worker audit events.</li>
            <li>Every claim is leased and fenced; stale workers cannot overwrite a later claim.</li>
            <li>Broker execution still needs local receipt and reconciliation hardening before live use.</li>
          </ul>
        </Card>
      </div>
    </>
  );
}

function EventBadge({ eventType }: { eventType: string }) {
  const lower = eventType.toLowerCase();
  const tone = lower.includes("failed") || lower.includes("rejected")
    ? "danger"
    : lower.includes("succeeded") || lower.includes("filled") || lower.includes("closed")
      ? "ok"
      : lower.includes("expired")
        ? "muted"
        : "info";
  return <Badge tone={tone}>{eventType.replace(/_/g, " ")}</Badge>;
}
