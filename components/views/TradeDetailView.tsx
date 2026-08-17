"use client";

import { useState } from "react";
import Link from "next/link";

import { api } from "@/lib/api/client";
import type { RuleCheck, TradeStage } from "@/lib/api/types";
import { Badge, Card, ErrorBanner, SideBadge, Spinner, Stat, StatusBadge } from "@/components/ui";
import {
  dateTime,
  guessDigits,
  lots,
  money,
  percent,
  price,
  ratio,
  signedMoney,
  time,
} from "@/lib/format";
import { useAsync } from "@/lib/useAsync";

/** The route segment supplies the id, so this view stays independent of routing. */
export default function TradeDetailView({ tradeId }: { tradeId: number }) {
  const id = tradeId;
  const { data, loading, error, reload } = useAsync(() => api.trade(id), [id]);
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [actionNote, setActionNote] = useState<string | null>(null);

  const act = async (label: string, run: () => Promise<{ actions: string[]; error: string }>) => {
    setBusy(label);
    setActionError(null);
    setActionNote(null);
    try {
      const result = await run();
      setActionNote(
        result.error ||
          (result.actions.length ? result.actions.join("; ") : "Nothing needed changing."),
      );
      reload();
    } catch (cause) {
      setActionError(cause);
    } finally {
      setBusy(null);
    }
  };

  if (loading) return <Spinner label="loading trade" />;
  if (error) return <ErrorBanner error={error} />;
  if (!data) return null;

  const trade = data;
  const digits = guessDigits(trade.entry_price || trade.requested_entry);
  const currency = trade.account_currency;
  const rMultiple = trade.planned_risk > 0 ? trade.realised_pl / trade.planned_risk : null;
  const checks = (trade.rules?.checks as RuleCheck[] | undefined) ?? [];
  const active = trade.status === "open" || trade.status === "scaling" || trade.status === "pending";

  return (
    <>
      <div className="page-head">
        <div>
          <h1>
            {trade.symbol} <SideBadge side={trade.side} /> <StatusBadge status={trade.status} />
          </h1>
          <p>
            Trade #{trade.id} · position{" "}
            <span className="mono">{trade.position_ticket ?? "-"}</span> · opened{" "}
            {dateTime(trade.opened_at)}
            {trade.closed_at && <> · closed {dateTime(trade.closed_at)}</>}
            {" · "}
            <Link href="/trades">back to list</Link>
          </p>
        </div>
        <div className="btn-group">
          <button className="btn btn-sm" onClick={() => void act("sync", () => api.syncTrade(id))} disabled={busy !== null}>
            {busy === "sync" ? "Syncing..." : "Sync with broker"}
          </button>
          {active && (
            <>
              <button
                className="btn btn-sm"
                onClick={() => void act("manage", () => api.manageTrade(id))}
                disabled={busy !== null}
              >
                {busy === "manage" ? "Running..." : "Run ladder pass"}
              </button>
              <button
                className="btn btn-sm btn-danger"
                onClick={() => void act("close", () => api.closeTrade(id))}
                disabled={busy !== null}
              >
                {busy === "close" ? "Closing..." : "Close at market"}
              </button>
            </>
          )}
        </div>
      </div>

      <ErrorBanner error={actionError} />
      {actionNote && <div className="banner banner-info">{actionNote}</div>}
      {trade.last_error && (
        <div className="banner banner-warn">
          <div className="banner-title">Last problem reported</div>
          {trade.last_error}
        </div>
      )}

      <div className="grid grid-3">
        <Stat
          label="Planned risk"
          value={money(trade.planned_risk, currency)}
          note={`${percent(trade.planned_risk_pct)} of ${money(trade.capital_at_entry, currency)}`}
        />
        <Stat
          label="Realised"
          value={signedMoney(trade.realised_pl, currency)}
          tone={trade.realised_pl > 0 ? "pos" : trade.realised_pl < 0 ? "neg" : "muted"}
          note={rMultiple === null ? undefined : `${ratio(rMultiple)} on planned risk`}
        />
        <Stat
          label="Volume"
          value={`${lots(trade.remaining_volume)} / ${lots(trade.initial_volume)}`}
          note="remaining / original"
        />
        <Stat label="Entry" value={price(trade.entry_price, digits)} note={`requested ${price(trade.requested_entry, digits)}`} />
        <Stat
          label="Stop"
          value={price(trade.current_stop, digits)}
          note={`opened at ${price(trade.initial_stop, digits)}`}
        />
        <Stat
          label="Planned profit"
          value={money(trade.planned_profit, currency)}
          note={trade.ladder_preset.replace(/_/g, " ")}
        />
      </div>

      <div className="grid grid-2 mt">
        <Card title="Ladder execution">
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Rung</th>
                  <th className="num">R</th>
                  <th className="num">Target</th>
                  <th className="num">Planned</th>
                  <th className="num">Filled</th>
                  <th className="num">Stop after</th>
                  <th className="num">Realised</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {trade.stages.map((stage) => (
                  <tr key={stage.stage_key} className={stage.status === "skipped" ? "dim" : undefined}>
                    <td className="strong">{stage.stage_key}</td>
                    <td className="num">1:{stage.r_multiple.toFixed(0)}</td>
                    <td className="num">{price(stage.target_price, digits)}</td>
                    <td className="num">{stage.planned_volume > 0 ? lots(stage.planned_volume) : "-"}</td>
                    <td className="num">{stage.executed_volume > 0 ? lots(stage.executed_volume) : "-"}</td>
                    <td className="num">{stage.sl_after === null ? "-" : price(stage.sl_after, digits)}</td>
                    <td className={`num ${stage.realised_pl >= 0 ? "pos" : "neg"}`}>
                      {stage.status === "filled" ? signedMoney(stage.realised_pl, currency) : "-"}
                    </td>
                    <td>
                      <StageBadge stage={stage} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {trade.stages.some((s) => s.note) && (
            <ul className="tiny faint mt" style={{ margin: "8px 0 0", paddingLeft: 18 }}>
              {trade.stages
                .filter((s) => s.note)
                .map((s) => (
                  <li key={s.stage_key}>
                    <strong>{s.stage_key}</strong> {s.note}
                  </li>
                ))}
            </ul>
          )}
        </Card>

        <Card title="Rules at approval" hint="The verdict recorded when this entry was authorised">
          {checks.length === 0 ? (
            <p className="muted mb-0">No rule snapshot stored for this trade.</p>
          ) : (
            checks.map((check) => (
              <div className="rule" key={check.code}>
                <div
                  className={`rule-icon ${check.passed ? "pos" : "neg"}`}
                  aria-hidden="true"
                >
                  {check.passed ? "\u2713" : "\u2717"}
                </div>
                <div className="rule-body">
                  <div className="rule-name">{check.rule}</div>
                  <div className="rule-msg">{check.message}</div>
                </div>
              </div>
            ))
          )}
        </Card>
      </div>

      <div className="mt">
        <Card
          title="Event log"
          actions={<Badge tone="muted">{trade.events.length}</Badge>}
          hint="Everything the platform did with this position, newest last"
        >
          {trade.events.length === 0 ? (
            <p className="muted mb-0">No events recorded.</p>
          ) : (
            <div className="event-list">
              {trade.events.map((event) => (
                <div className="event" key={event.id}>
                  <span className="event-time">{time(event.created_at)}</span>
                  <span>
                    <Badge tone={eventTone(event.event_type)}>
                      {event.event_type.replace(/_/g, " ")}
                    </Badge>{" "}
                    {event.message}
                  </span>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>
    </>
  );
}

function StageBadge({ stage }: { stage: TradeStage }) {
  switch (stage.status) {
    case "filled":
      return <Badge tone="ok">filled</Badge>;
    case "failed":
      return <Badge tone="danger">failed</Badge>;
    case "skipped":
      return <Badge tone="muted">skipped</Badge>;
    default:
      return <Badge tone="info">waiting</Badge>;
  }
}

function eventTone(kind: string): "ok" | "warn" | "danger" | "info" | "muted" {
  if (kind === "error" || kind === "order_failed" || kind === "rejected") return "danger";
  if (kind === "stop_hit" || kind === "sync") return "warn";
  if (kind === "partial_close" || kind === "order_filled" || kind === "position_closed") return "ok";
  if (kind === "validated" || kind === "sl_modified") return "info";
  return "muted";
}
