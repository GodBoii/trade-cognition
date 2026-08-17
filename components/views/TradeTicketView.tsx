"use client";

import Link from "next/link";
import { useMemo, useRef, useState } from "react";

import { Badge, Banner, Card, ErrorBanner, Field } from "@/components/ui";
import { enqueueTradeIntent } from "@/lib/supabase/data";
import type { TradeIntent } from "@/lib/supabase/types";
import { useTrading } from "@/state/trading";

type StopMode = "points" | "price";

export default function TradeTicketView() {
  const { connection, status, rules, refresh } = useTrading();
  const [symbol, setSymbol] = useState("EURUSD");
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [orderKind, setOrderKind] = useState<"market" | "limit" | "stop">("market");
  const [entryPrice, setEntryPrice] = useState("");
  const [stopMode, setStopMode] = useState<StopMode>("points");
  const [stopPoints, setStopPoints] = useState("500");
  const [stopLoss, setStopLoss] = useState("");
  const [comment, setComment] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [result, setResult] = useState<TradeIntent | null>(null);
  const requestId = useRef<string | null>(null);

  const numericEntry = Number(entryPrice);
  const numericStopPoints = Number(stopPoints);
  const numericStopLoss = Number(stopLoss);
  const stopValid =
    stopMode === "points" ? numericStopPoints > 0 : numericStopLoss > 0;
  const entryRequired = orderKind !== "market";
  const canSubmit = Boolean(
    connection?.is_enabled && symbol.trim() && stopValid && (!entryRequired || numericEntry > 0),
  );

  const capital = useMemo(() => {
    if (!connection) return null;
    if (rules?.capital_basis === "fixed") return rules.fixed_capital;
    if (rules?.capital_basis === "equity") return connection.last_equity;
    return connection.last_balance;
  }, [connection, rules]);
  const indicativeVolume = capital && capital > 0 ? (capital / 1000) * 0.02 : null;
  const maxLoss = capital && capital > 0 ? capital * 0.02 : null;

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!connection || !canSubmit) return;
    setSubmitting(true);
    setError(null);
    setResult(null);
    requestId.current ??= crypto.randomUUID();
    try {
      const intent = await enqueueTradeIntent({
        connectionId: connection.id,
        clientRequestId: requestId.current,
        symbol,
        side,
        orderKind,
        requestedEntry: numericEntry > 0 ? numericEntry : null,
        stopLoss: stopMode === "price" ? numericStopLoss : null,
        stopPoints: stopMode === "points" ? numericStopPoints : null,
        requestedVolume: null,
        comment,
      });
      setResult(intent);
      requestId.current = null;
      await refresh();
    } catch (cause) {
      // Retain the same UUID so retrying after an uncertain network response is idempotent.
      setError(cause);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>New trade instruction</h1>
          <p>
            Save an expiring instruction in Supabase. The local worker uses current MT5 prices,
            symbol limits, positions, and margin to approve or reject it before execution.
          </p>
        </div>
        <Badge tone={status === "online" ? "ok" : "warn"}>worker {status}</Badge>
      </div>

      <ErrorBanner error={error} />
      {!connection && (
        <Banner tone="warn" title="Pair an MT5 worker first">
          The full website is available, but a trade cannot be queued until a connection exists. {" "}
          <Link href="/accounts">Open Accounts</Link>.
        </Banner>
      )}
      {connection && status !== "online" && (
        <Banner tone="warn" title="The worker is not currently online">
          You may queue an instruction, but it expires after five minutes. Queued does not mean
          approved or executed, and a stale instruction will be marked expired.
        </Banner>
      )}
      {result && (
        <Banner tone="ok" title="Instruction saved durably">
          <p className="small">
            <strong>{result.symbol}</strong> is <strong>{result.status}</strong>. Closing this tab
            will not lose it. The worker still has to validate every rule before execution.
          </p>
          <Link className="btn btn-sm" href="/trades">
            View trade queue
          </Link>
        </Banner>
      )}

      <div className="grid grid-2 mt">
        <Card title="Instruction">
          <form onSubmit={submit}>
            <Field label="MT5 connection">
              <input
                type="text"
                value={
                  connection
                    ? `${connection.label} · ${connection.mt5_login ?? "awaiting MT5"}`
                    : "No connection selected"
                }
                readOnly
              />
            </Field>

            <Field label="Derivative / symbol" hint="Use the exact MT5 symbol, including broker suffixes">
              <input
                value={symbol}
                onChange={(event) => setSymbol(event.target.value.toUpperCase())}
                maxLength={40}
                required
              />
            </Field>

            <div className="grid grid-2">
              <Field label="Side">
                <select value={side} onChange={(event) => setSide(event.target.value as "buy" | "sell")}>
                  <option value="buy">Buy</option>
                  <option value="sell">Sell</option>
                </select>
              </Field>
              <Field label="Order kind">
                <select
                  value={orderKind}
                  onChange={(event) =>
                    setOrderKind(event.target.value as "market" | "limit" | "stop")
                  }
                >
                  <option value="market">Market</option>
                  <option value="limit" disabled>
                    Limit (real MT5 adapter pending)
                  </option>
                  <option value="stop" disabled>
                    Stop order (real MT5 adapter pending)
                  </option>
                </select>
              </Field>
            </div>

            <Field
              label={entryRequired ? "Requested entry price" : "Entry-price reference (optional)"}
              hint="The worker rejects an invalid or stale requested price"
            >
              <input
                type="number"
                min={0}
                step="any"
                value={entryPrice}
                onChange={(event) => setEntryPrice(event.target.value)}
                required={entryRequired}
              />
            </Field>

            <Field label="Stop-loss input">
              <select value={stopMode} onChange={(event) => setStopMode(event.target.value as StopMode)}>
                <option value="points">Distance in MT5 points</option>
                <option value="price">Absolute stop price</option>
              </select>
            </Field>

            {stopMode === "points" ? (
              <Field label="Stop distance (points)">
                <input
                  type="number"
                  min={1}
                  step={1}
                  value={stopPoints}
                  onChange={(event) => setStopPoints(event.target.value)}
                  required
                />
              </Field>
            ) : (
              <Field label="Stop-loss price">
                <input
                  type="number"
                  min={0}
                  step="any"
                  value={stopLoss}
                  onChange={(event) => setStopLoss(event.target.value)}
                  required
                />
              </Field>
            )}

            <Field label="Comment" hint="Optional; maximum 48 characters">
              <input
                value={comment}
                onChange={(event) => setComment(event.target.value)}
                maxLength={48}
              />
            </Field>

            <button
              className="btn btn-primary btn-block"
              type="submit"
              disabled={!canSubmit || submitting}
            >
              {submitting ? "Saving instruction..." : "Queue for worker validation"}
            </button>
          </form>
        </Card>

        <div className="stack">
          <Card title="Pre-trade estimate">
            {capital === null ? (
              <p className="muted mb-0">
                No fresh account snapshot exists. Balance, exact lot step, monetary stop risk,
                margin, spread, and targets are intentionally not shown as zero.
              </p>
            ) : (
              <dl className="kv">
                <dt>Last reported capital</dt>
                <dd>{capital.toFixed(2)} {connection?.currency || ""}</dd>
                <dt>Formula allocation</dt>
                <dd>{indicativeVolume?.toFixed(4)} lots before broker rounding</dd>
                <dt>Maximum stop loss</dt>
                <dd>{maxLoss?.toFixed(2)} {connection?.currency || ""} (2%)</dd>
              </dl>
            )}
            <p className="tiny faint mb-0 mt">
              This is an indication from the last Supabase snapshot, not approval. Only the worker
              can calculate tick-value risk and required margin from live MT5 data.
            </p>
          </Card>

          <Card title="Mandatory execution criteria">
            <ul className="small muted" style={{ margin: 0, paddingLeft: 18 }}>
              <li>No other active instruction or position for this user and symbol.</li>
              <li>Exactly 0.02 lots per 1,000 capital, quantized to the MT5 lot grid.</li>
              <li>Loss at the hard stop no greater than 2% of capital.</li>
              <li>Enough free margin and valid broker stop/volume constraints.</li>
              <li>TP sequence: 50% at 1R, 25% at 2R, final 25% at 3R.</li>
            </ul>
          </Card>

          <Card title="Status meanings">
            <p className="small muted mb-0">
              <strong>Queued</strong> means saved only. <strong>Claimed/validating</strong> means the
              local worker is checking MT5. <strong>Submitted/open</strong> means a broker action
              succeeded. Rejected, failed, cancelled, and expired never mean a trade was placed.
            </p>
          </Card>
        </div>
      </div>
    </>
  );
}
