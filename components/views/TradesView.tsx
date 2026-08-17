"use client";

import { useState } from "react";
import Link from "next/link";

import { api } from "@/lib/api/client";
import { Badge, Card, Empty, ErrorBanner, SideBadge, Spinner, StatusBadge } from "@/components/ui";
import { LadderProgress } from "@/components/LadderProgress";
import { dateTime, guessDigits, lots, money, percent, price, ratio, signedMoney } from "@/lib/format";
import { useAsync } from "@/lib/useAsync";

export default function TradesView() {
  const [activeOnly, setActiveOnly] = useState(false);
  const { data, loading, error, reload } = useAsync(
    () => api.trades({ active_only: activeOnly, limit: 200 }),
    [activeOnly],
  );

  const trades = data ?? [];
  const realised = trades
    .filter((t) => t.status === "closed")
    .reduce((total, t) => total + t.realised_pl, 0);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Trades</h1>
          <p>
            Every trade the platform has managed, with the risk it was approved at and what it
            actually returned.
          </p>
        </div>
        <div className="inline">
          <label className="checkbox" style={{ margin: 0 }}>
            <input
              type="checkbox"
              checked={activeOnly}
              onChange={(e) => setActiveOnly(e.target.checked)}
            />
            <span>Active only</span>
          </label>
          <button className="btn btn-sm" onClick={reload}>
            Refresh
          </button>
        </div>
      </div>

      <ErrorBanner error={error} />

      <Card
        title="History"
        actions={
          <span className="inline">
            <Badge tone="muted">{trades.length}</Badge>
            {realised !== 0 && (
              <Badge tone={realised > 0 ? "ok" : "danger"}>
                {signedMoney(realised, trades[0]?.account_currency ?? "USD")} realised
              </Badge>
            )}
          </span>
        }
      >
        {loading ? (
          <Spinner label="loading trades" />
        ) : trades.length === 0 ? (
          <Empty>
            No trades yet. <Link href="/trade">Plan one</Link>.
          </Empty>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Opened</th>
                  <th>Symbol</th>
                  <th>Side</th>
                  <th>Status</th>
                  <th className="num">Volume</th>
                  <th className="num">Entry</th>
                  <th className="num">Stop</th>
                  <th className="num">Risk</th>
                  <th className="num">Result</th>
                  <th className="num">R</th>
                  <th>Ladder</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {trades.map((trade) => {
                  const digits = guessDigits(trade.entry_price || trade.requested_entry);
                  const rMultiple =
                    trade.planned_risk > 0 ? trade.realised_pl / trade.planned_risk : null;
                  return (
                    <tr key={trade.id}>
                      <td className="small nowrap">{dateTime(trade.opened_at ?? trade.created_at)}</td>
                      <td className="strong">{trade.symbol}</td>
                      <td>
                        <SideBadge side={trade.side} />
                      </td>
                      <td>
                        <StatusBadge status={trade.status} />
                        {trade.close_reason && (
                          <div className="tiny faint">{trade.close_reason.replace(/_/g, " ")}</div>
                        )}
                      </td>
                      <td className="num">
                        {lots(trade.remaining_volume)}
                        <div className="tiny faint">of {lots(trade.initial_volume)}</div>
                      </td>
                      <td className="num">{price(trade.entry_price, digits)}</td>
                      <td className="num">{price(trade.current_stop, digits)}</td>
                      <td className="num">
                        {money(trade.planned_risk, trade.account_currency)}
                        <div className="tiny faint">{percent(trade.planned_risk_pct)}</div>
                      </td>
                      <td
                        className={`num ${
                          trade.realised_pl > 0 ? "pos" : trade.realised_pl < 0 ? "neg" : ""
                        }`}
                      >
                        {trade.realised_pl === 0 && trade.status !== "closed"
                          ? "-"
                          : signedMoney(trade.realised_pl, trade.account_currency)}
                      </td>
                      <td className="num">{rMultiple === null ? "-" : ratio(rMultiple)}</td>
                      <td style={{ minWidth: 190 }}>
                        <LadderProgress trade={trade} />
                      </td>
                      <td className="right">
                        <Link className="btn btn-sm" href={`/trades/${trade.id}`}>
                          Detail
                        </Link>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}
