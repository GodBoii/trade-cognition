"use client";

import Link from "next/link";

import { api } from "@/lib/api/client";
import type { PositionRow } from "@/lib/api/types";
import { LadderProgress } from "@/components/LadderProgress";
import { Badge, Card, Empty, ErrorBanner, SideBadge, Stat } from "@/components/ui";
import { guessDigits, lots, money, percent, price, signedMoney, time } from "@/lib/format";
import { useAsync } from "@/lib/useAsync";
import { useAuth } from "@/state/auth";
import { useStream } from "@/state/useStream";

export default function DashboardView() {
  const { accountId, account } = useAuth();
  const { snapshot, status, error, refresh } = useStream(accountId);
  const performance = useAsync(() => api.performance(30), [accountId]);

  const currency = snapshot?.account.currency ?? account?.currency ?? "USD";
  const equity = snapshot?.account.equity ?? 0;
  const capital = snapshot?.capital ?? 0;
  const rows = snapshot?.positions ?? [];
  const floating = rows.reduce((total, row) => total + row.position.profit, 0);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Dashboard</h1>
          <p>
            Live account state and every open position. Managed positions show the ladder rung they
            have reached; the monitor advances them automatically.
          </p>
        </div>
        <div className="inline">
          <FeedIndicator status={status} at={snapshot?.server_time} />
          <button className="btn btn-sm" onClick={refresh}>
            Refresh
          </button>
        </div>
      </div>

      {error && (
        <div className="banner banner-warn">
          <div className="banner-title">Live feed problem</div>
          {error}
        </div>
      )}

      <div className="grid grid-3">
        <Stat
          label="Balance"
          value={money(snapshot?.account.balance ?? 0, currency)}
          note={account ? `${account.login} · ${account.server}` : undefined}
        />
        <Stat
          label="Equity"
          value={money(equity, currency)}
          note={`Floating ${signedMoney(floating, currency)}`}
          tone={floating === 0 ? undefined : floating > 0 ? "pos" : "neg"}
        />
        <Stat
          label="Free margin"
          value={money(snapshot?.account.margin_free ?? 0, currency)}
          note={`Used ${money(snapshot?.account.margin ?? 0, currency)}`}
        />
        <Stat
          label="Trading capital"
          value={money(capital, currency)}
          note={`Allocation ${lots((capital / 1000) * 0.02)} lots per entry at 0.02/1,000`}
        />
        <Stat
          label="Risk ceiling per trade"
          value={money(snapshot?.max_risk_money ?? 0, currency)}
          note="Rule 3 blocks anything larger"
        />
        <Stat
          label="Risk currently on"
          value={money(snapshot?.risk_on ?? 0, currency)}
          note={
            capital > 0
              ? `${percent(((snapshot?.risk_on ?? 0) / capital) * 100, 2)} of capital`
              : undefined
          }
          tone={(snapshot?.risk_on ?? 0) > 0 ? "neg" : undefined}
        />
      </div>

      <div className="mt">
        <Card
          title="Open positions"
          actions={<Badge tone="muted">{rows.length} live</Badge>}
        >
          {rows.length === 0 ? (
            <Empty>
              Nothing open. <Link href="/trade">Plan a trade</Link> to get started.
            </Empty>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th>Side</th>
                    <th className="num">Volume</th>
                    <th className="num">Entry</th>
                    <th className="num">Price</th>
                    <th className="num">Stop</th>
                    <th className="num">Target</th>
                    <th className="num">P/L</th>
                    <th>Ladder</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <PositionRowView key={row.position.ticket} row={row} currency={currency} />
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {(snapshot?.active_trades.length ?? 0) > 0 && rows.length === 0 && (
            <p className="tiny faint">
              Managed trades exist without a matching position; the monitor will reconcile them on
              its next pass.
            </p>
          )}
        </Card>
      </div>

      <div className="grid grid-2 mt">
        <Card title="Discipline (last 30 days)" hint="How often the rules had to intervene">
          <ErrorBanner error={performance.error} />
          {performance.data && (
            <dl className="kv">
              <dt>Closed trades</dt>
              <dd>{performance.data.closed_trades}</dd>
              <dt>Win rate</dt>
              <dd>{percent(performance.data.win_rate_pct, 1)}</dd>
              <dt>Net P/L</dt>
              <dd className={performance.data.net_pl >= 0 ? "pos" : "neg"}>
                {signedMoney(performance.data.net_pl, currency)}
              </dd>
              <dt>Profit factor</dt>
              <dd>{performance.data.profit_factor?.toFixed(2) ?? "-"}</dd>
              <div className="divider" />
              <dt>Entries approved</dt>
              <dd>{performance.data.decisions_approved}</dd>
              <dt>Entries blocked by rules</dt>
              <dd className={performance.data.decisions_rejected > 0 ? "neg" : ""}>
                {performance.data.decisions_rejected}
              </dd>
              <dt>Rule adherence</dt>
              <dd>{percent(performance.data.rule_adherence_pct, 1)}</dd>
            </dl>
          )}
          {performance.data?.top_rejections.length ? (
            <>
              <h3 className="mt">Most common blocks</h3>
              <ul className="small muted" style={{ margin: "6px 0 0", paddingLeft: 18 }}>
                {performance.data.top_rejections.map((entry) => (
                  <li key={entry.codes}>
                    <span className="mono tiny">{entry.codes}</span> — {entry.count}
                  </li>
                ))}
              </ul>
            </>
          ) : null}
        </Card>

        <Card title="Account health">
          <dl className="kv">
            <dt>Margin level</dt>
            <dd>{percent(snapshot?.account.margin_level ?? 0, 1)}</dd>
            <dt>Leverage</dt>
            <dd>1:{snapshot?.account.leverage ?? account?.leverage ?? 0}</dd>
            <dt>Trading permitted</dt>
            <dd>{snapshot?.account.trade_allowed ? "yes" : "no"}</dd>
            <dt>Algo trading enabled</dt>
            <dd>{snapshot?.account.trade_expert ? "yes" : "no"}</dd>
            <dt>Broker</dt>
            <dd className="small">{snapshot?.account.company || account?.company || "-"}</dd>
          </dl>
          {snapshot && !snapshot.account.trade_expert && (
            <div className="banner banner-warn mt">
              Algo trading is disabled in the terminal, so orders will be refused. Enable it in the
              MT5 toolbar.
            </div>
          )}
        </Card>
      </div>
    </>
  );
}

function FeedIndicator({ status, at }: { status: string; at?: string }) {
  const dot = status === "live" ? "on" : status === "error" ? "off" : "";
  const label =
    status === "live"
      ? `live · ${time(at)}`
      : status === "polling"
        ? `polling · ${time(at)}`
        : status;
  return (
    <span className="live">
      <span className={`live-dot ${dot}`} aria-hidden="true" />
      {label}
    </span>
  );
}

function PositionRowView({ row, currency }: { row: PositionRow; currency: string }) {
  const { position, trade } = row;
  const digits = guessDigits(position.price_open);

  return (
    <tr>
      <td>
        <span className="strong">{position.symbol}</span>
        <div className="tiny faint mono">#{position.ticket}</div>
      </td>
      <td>
        <SideBadge side={position.side} />
      </td>
      <td className="num">
        {lots(position.volume)}
        {trade && trade.initial_volume !== position.volume && (
          <div className="tiny faint">of {lots(trade.initial_volume)}</div>
        )}
      </td>
      <td className="num">{price(position.price_open, digits)}</td>
      <td className="num">{price(position.price_current, digits)}</td>
      <td className="num">{position.sl ? price(position.sl, digits) : <span className="neg">none</span>}</td>
      <td className="num">{position.tp ? price(position.tp, digits) : "-"}</td>
      <td className={`num ${position.profit >= 0 ? "pos" : "neg"}`}>
        {signedMoney(position.profit, currency)}
      </td>
      <td style={{ minWidth: 210 }}>
        {trade ? <LadderProgress trade={trade} /> : <Badge tone="warn">unmanaged</Badge>}
      </td>
      <td className="right">
        {trade && (
          <Link className="btn btn-sm" href={`/trades/${trade.id}`}>
            Open
          </Link>
        )}
      </td>
    </tr>
  );
}

