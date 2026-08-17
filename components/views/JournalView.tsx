"use client";

import { useState } from "react";
import Link from "next/link";

import { api } from "@/lib/api/client";
import type { DecisionDetail } from "@/lib/api/types";
import { Badge, Card, Empty, ErrorBanner, SideBadge, Spinner } from "@/components/ui";
import { dateTime, lots, money, percent, ratio, time } from "@/lib/format";
import { useAsync } from "@/lib/useAsync";

type Filter = "all" | "approved" | "rejected";

export default function JournalView() {
  const [filter, setFilter] = useState<Filter>("all");
  const [selected, setSelected] = useState<DecisionDetail | null>(null);
  const [detailError, setDetailError] = useState<unknown>(null);

  const decisions = useAsync(
    () =>
      api.decisions({
        approved: filter === "all" ? undefined : filter === "approved",
        limit: 200,
      }),
    [filter],
  );
  const events = useAsync(() => api.events({ limit: 120 }), []);

  const open = async (id: number) => {
    setDetailError(null);
    try {
      setSelected(await api.decision(id));
    } catch (cause) {
      setDetailError(cause);
    }
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Journal</h1>
          <p>
            Every pre-trade decision, including the entries the rules refused. This is the record of
            what the platform saw and why it acted.
          </p>
        </div>
        <div className="segmented" style={{ maxWidth: 300 }}>
          {(["all", "approved", "rejected"] as Filter[]).map((option) => (
            <button
              key={option}
              type="button"
              className={filter === option ? "on-long" : ""}
              onClick={() => setFilter(option)}
              aria-pressed={filter === option}
            >
              {option}
            </button>
          ))}
        </div>
      </div>

      <ErrorBanner error={decisions.error} />
      <ErrorBanner error={detailError} />

      <Card title="Decisions">
        {decisions.loading ? (
          <Spinner label="loading decisions" />
        ) : (decisions.data ?? []).length === 0 ? (
          <Empty>No decisions recorded for this filter.</Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>When</th>
                  <th>Symbol</th>
                  <th>Side</th>
                  <th className="num">Lots</th>
                  <th className="num">Risk</th>
                  <th className="num">% cap</th>
                  <th className="num">R/R</th>
                  <th>Verdict</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {(decisions.data ?? []).map((decision) => (
                  <tr key={decision.id} className="clickable" onClick={() => void open(decision.id)}>
                    <td className="small nowrap">{dateTime(decision.created_at)}</td>
                    <td className="strong">{decision.symbol}</td>
                    <td>
                      <SideBadge side={decision.side} />
                    </td>
                    <td className="num">{lots(decision.volume)}</td>
                    <td className="num">{money(decision.max_loss)}</td>
                    <td className="num">{percent(decision.risk_pct)}</td>
                    <td className="num">{ratio(decision.reward_risk)}</td>
                    <td>
                      {decision.approved ? (
                        <Badge tone={decision.executed ? "ok" : "warn"}>
                          {decision.executed ? "executed" : "approved"}
                        </Badge>
                      ) : (
                        <Badge tone="danger">blocked</Badge>
                      )}
                    </td>
                    <td className="small">
                      {decision.violation_codes ? (
                        <span className="mono tiny">{decision.violation_codes}</span>
                      ) : (
                        <span className="faint">—</span>
                      )}
                      {decision.trade_id && (
                        <>
                          {" "}
                          <Link href={`/trades/${decision.trade_id}`}>
                        trade #{decision.trade_id}
                      </Link>
                        </>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="tiny faint mb-0 mt">Select a row to see every rule check for that decision.</p>
      </Card>

      {selected && (
        <div className="mt">
          <Card
            title={`Decision #${selected.id} — ${selected.symbol} ${selected.side}`}
            actions={
              <button className="btn btn-sm" onClick={() => setSelected(null)}>
                Close
              </button>
            }
          >
            <p className="small muted">{selected.summary}</p>
            {selected.checks.map((check) => (
              <div className="rule" key={check.code}>
                <div className={`rule-icon ${check.passed ? "pos" : "neg"}`} aria-hidden="true">
                  {check.passed ? "\u2713" : "\u2717"}
                </div>
                <div className="rule-body">
                  <div className="rule-name">{check.rule}</div>
                  <div className="rule-msg">{check.message}</div>
                  <div className="rule-code">{check.code}</div>
                </div>
              </div>
            ))}
          </Card>
        </div>
      )}

      <div className="mt">
        <Card title="Activity" hint="Orders, partial exits, stop moves and reconciliations">
          {events.loading ? (
            <Spinner label="loading activity" />
          ) : (events.data ?? []).length === 0 ? (
            <Empty>Nothing has happened yet.</Empty>
          ) : (
            <div className="event-list">
              {(events.data ?? []).map((event) => (
                <div className="event" key={event.id}>
                  <span className="event-time">{time(event.created_at)}</span>
                  <span>
                    <Badge tone="muted">{event.event_type.replace(/_/g, " ")}</Badge>{" "}
                    {event.message}
                    {event.trade_id && (
                      <>
                        {" "}
                        <Link className="tiny" href={`/trades/${event.trade_id}`}>
                          #{event.trade_id}
                        </Link>
                      </>
                    )}
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
