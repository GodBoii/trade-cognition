"use client";

import Link from "next/link";

import { Badge, Banner, Card, Empty, ErrorBanner, SideBadge, Stat, Spinner } from "@/components/ui";
import { dateTime, money } from "@/lib/format";
import { useTrading } from "@/state/trading";

const ACTIVE_STATES = new Set(["queued", "claimed", "validating", "submitted", "open", "scaling"]);

export default function DashboardView() {
  const {
    connection,
    worker,
    status,
    rules,
    recentIntents,
    loading,
    error,
    refresh,
  } = useTrading();
  const active = recentIntents.filter((item) => ACTIVE_STATES.has(item.status));
  const currency = connection?.currency || "USD";
  const capital =
    rules?.capital_basis === "fixed"
      ? rules.fixed_capital
      : rules?.capital_basis === "equity"
        ? connection?.last_equity ?? null
        : connection?.last_balance ?? null;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Dashboard</h1>
          <p>
            Last durable MT5 snapshot and execution state from Supabase. The website remains usable
            without a direct connection to your local worker.
          </p>
        </div>
        <div className="inline">
          <Badge tone={status === "online" ? "ok" : status === "error" ? "danger" : "warn"}>
            {status}
          </Badge>
          <button className="btn btn-sm" onClick={() => void refresh()} disabled={loading}>
            {loading ? "Refreshing..." : "Refresh"}
          </button>
        </div>
      </div>

      <ErrorBanner error={error} />
      {!connection && !loading && (
        <Banner tone="warn" title="No MT5 worker is paired">
          You can review rules and the complete website now. Pair a local worker from {" "}
          <Link href="/accounts">Accounts</Link> before submitting an instruction.
        </Banner>
      )}
      {connection && status !== "online" && (
        <Banner tone="warn" title={`Worker is ${status}`}>
          Last-known values below are not live. Missing values are shown as unavailable, never as a
          genuine zero balance. Broker-side hard SL/TP continues independently.
        </Banner>
      )}

      {loading && !connection ? (
        <Spinner label="loading Supabase control plane" />
      ) : (
        <div className="grid grid-3">
          <SnapshotStat
            label="Balance"
            value={connection?.last_balance ?? null}
            currency={currency}
            note={connection?.mt5_login ? `${connection.mt5_login} · ${connection.server}` : "No MT5 snapshot"}
          />
          <SnapshotStat
            label="Equity"
            value={connection?.last_equity ?? null}
            currency={currency}
            note="Last worker report"
          />
          <SnapshotStat
            label="Free margin"
            value={connection?.last_free_margin ?? null}
            currency={currency}
            note="Exact requirement checked before entry"
          />
          <SnapshotStat
            label="Trading capital"
            value={capital}
            currency={currency}
            note={`${rules?.capital_basis ?? "balance"} basis`}
          />
          <SnapshotStat
            label="Maximum loss per trade"
            value={capital === null ? null : capital * 0.02}
            currency={currency}
            note="2% hard ceiling"
          />
          <Stat
            label="Formula lot allocation"
            value={capital === null ? "Unavailable" : `${((capital / 1000) * 0.02).toFixed(4)} lots`}
            note="Before broker lot-step rounding"
          />
        </div>
      )}

      <div className="grid grid-2 mt">
        <Card title="Active instructions" actions={<Badge tone="muted">{active.length}</Badge>}>
          {active.length === 0 ? (
            <Empty>
              No active instructions. <Link href="/trade">Plan a trade</Link>.
            </Empty>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr><th>Symbol</th><th>Side</th><th>Status</th><th>Updated</th></tr>
                </thead>
                <tbody>
                  {active.map((item) => (
                    <tr key={item.id}>
                      <td className="strong">{item.symbol}</td>
                      <td><SideBadge side={item.side} /></td>
                      <td><Badge tone={item.status === "open" ? "ok" : "warn"}>{item.status}</Badge></td>
                      <td className="small nowrap">{dateTime(item.updated_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card title="Automation readiness">
          <dl className="kv">
            <dt>Connection</dt><dd>{connection?.label ?? "not paired"}</dd>
            <dt>Worker</dt><dd>{worker?.name ?? "not registered"}</dd>
            <dt>Last worker heartbeat</dt>
            <dd>{worker?.last_seen_at ? dateTime(worker.last_seen_at) : "never"}</dd>
            <dt>Last MT5 snapshot</dt>
            <dd>{connection?.last_seen_at ? dateTime(connection.last_seen_at) : "never"}</dd>
            <dt>Automation</dt><dd>{connection?.is_enabled ? "enabled" : "paused / unavailable"}</dd>
          </dl>
          <p className="tiny faint mb-0 mt">
            A green online state means a recent heartbeat, not a promise that a future broker order
            will pass. Every trade is revalidated at execution time.
          </p>
        </Card>
      </div>
    </>
  );
}

function SnapshotStat({
  label,
  value,
  currency,
  note,
}: {
  label: string;
  value: number | null;
  currency: string;
  note: string;
}) {
  return <Stat label={label} value={value === null ? "Unavailable" : money(value, currency)} note={note} />;
}
