"use client";

/**
 * The trade ticket.
 *
 * Every parameter change re-runs `POST /api/calculator/preview` (debounced), so
 * the risk figures and the rule verdict on screen are produced by the same code
 * path that will authorise the order. The submit button is disabled while any
 * rule blocks the entry - the platform's whole purpose is that this cannot be
 * clicked through.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { api } from "@/lib/api/client";
import type {
  Assessment,
  LadderPreset,
  Side,
  StopScanRow,
  Submission,
  SymbolBrief,
} from "@/lib/api/types";
import { LadderTable, PlanWarnings, RiskSummary, RuleChecklist } from "@/components/PlanView";
import { Badge, Banner, Card, ErrorBanner, Field, Spinner } from "@/components/ui";
import { lots, money, percent, points, price } from "@/lib/format";
import { useAsync, useDebounced } from "@/lib/useAsync";
import { useAuth } from "@/state/auth";

type StopMode = "points" | "price";

export default function TradeTicketView() {
  const router = useRouter();
  const { accountId } = useAuth();

  const [symbol, setSymbol] = useState("EURUSD");
  const [search, setSearch] = useState("");
  const [side, setSide] = useState<Side>("buy");
  const [stopMode, setStopMode] = useState<StopMode>("points");
  const [stopPoints, setStopPoints] = useState("500");
  const [stopPrice, setStopPrice] = useState("");
  const [entryOverride, setEntryOverride] = useState("");
  const [volumeOverride, setVolumeOverride] = useState("");
  const [ladderPreset, setLadderPreset] = useState<LadderPreset | "">("");
  const [comment, setComment] = useState("");
  const [override, setOverride] = useState(false);

  const [assessment, setAssessment] = useState<Assessment | null>(null);
  const [previewError, setPreviewError] = useState<unknown>(null);
  const [previewing, setPreviewing] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<unknown>(null);
  const [result, setResult] = useState<Submission | null>(null);
  const [scan, setScan] = useState<StopScanRow[] | null>(null);

  const symbols = useAsync(() => api.symbols(undefined, accountId ?? undefined), [accountId]);
  const spec = useAsync(
    () => api.symbolSpec(symbol, accountId ?? undefined),
    [symbol, accountId],
  );
  const profile = useAsync(() => api.profile(), []);

  // The request body, memoised so the preview only re-runs on real changes.
  const request = useMemo(() => {
    const stopFromPoints = stopMode === "points" ? Number(stopPoints) : null;
    const stopFromPrice = stopMode === "price" ? Number(stopPrice) : null;
    return {
      symbol,
      side,
      stop_points: stopFromPoints && stopFromPoints > 0 ? stopFromPoints : null,
      stop_loss: stopFromPrice && stopFromPrice > 0 ? stopFromPrice : null,
      entry_price: Number(entryOverride) > 0 ? Number(entryOverride) : null,
      volume: Number(volumeOverride) > 0 ? Number(volumeOverride) : null,
      ladder_preset: ladderPreset === "" ? null : ladderPreset,
      account_id: accountId,
      override,
      comment,
    };
  }, [
    symbol,
    side,
    stopMode,
    stopPoints,
    stopPrice,
    entryOverride,
    volumeOverride,
    ladderPreset,
    accountId,
    override,
    comment,
  ]);

  const debounced = useDebounced(request, 350);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const hasStop = Boolean(debounced.stop_points || debounced.stop_loss);
    if (!debounced.symbol || !hasStop) {
      setAssessment(null);
      setPreviewError(null);
      return;
    }

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setPreviewing(true);
    api
      .preview(debounced, controller.signal)
      .then((next) => {
        setAssessment(next);
        setPreviewError(null);
      })
      .catch((cause) => {
        if ((cause as Error).name === "AbortError") return;
        setAssessment(null);
        setPreviewError(cause);
      })
      .finally(() => {
        if (!controller.signal.aborted) setPreviewing(false);
      });

    return () => controller.abort();
  }, [debounced]);

  const plan = assessment?.plan ?? null;
  const rules = assessment?.rules ?? null;
  const blocked = rules ? !rules.approved : true;
  const canOverride = Boolean(
    profile.data?.allow_manual_override &&
      rules?.checks.some((c) => !c.passed && c.overridable),
  );

  const filtered = (symbols.data ?? []).filter((s) => {
    const needle = search.trim().toLowerCase();
    if (!needle) return true;
    return (
      s.name.toLowerCase().includes(needle) || s.description.toLowerCase().includes(needle)
    );
  });

  const submit = async () => {
    setSubmitting(true);
    setSubmitError(null);
    setResult(null);
    try {
      const submission = await api.submitTrade(request);
      setResult(submission);
      if (submission.executed && submission.trade) {
        router.push(`/trades/${submission.trade.id}`);
      }
    } catch (cause) {
      setSubmitError(cause);
    } finally {
      setSubmitting(false);
    }
  };

  const runScan = async () => {
    try {
      const base = Number(stopPoints) || 500;
      const candidates = [0.25, 0.5, 0.75, 1, 1.5, 2, 3].map((m) => Math.round(base * m));
      setScan(await api.stopScan(symbol, side, candidates, accountId ?? undefined));
    } catch (cause) {
      setPreviewError(cause);
    }
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>New trade</h1>
          <p>
            Enter the parameters; the calculator and every rule run on each change. Nothing reaches
            the broker until all three rules pass.
          </p>
        </div>
        {previewing && <Spinner label="recalculating" />}
      </div>

      <div className="grid-ticket">
        {/* ------------------------------------------------ parameters */}
        <div className="stack">
          <Card title="Parameters">
            <Field label="Derivative">
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder={`Search symbols (${symbol} selected)`}
              />
            </Field>
            {symbols.loading ? (
              <Spinner label="loading symbols" />
            ) : (
              <div className="scroll-list" style={{ maxHeight: 168 }}>
                {filtered.slice(0, 60).map((entry) => (
                  <SymbolOption
                    key={entry.name}
                    entry={entry}
                    selected={entry.name === symbol}
                    onSelect={() => setSymbol(entry.name)}
                  />
                ))}
                {filtered.length === 0 && <div className="empty">No symbol matches.</div>}
              </div>
            )}

            <div className="field mt">
              <label htmlFor="side-buy">Direction</label>
              <div className="segmented">
                <button
                  id="side-buy"
                  type="button"
                  className={side === "buy" ? "on-long" : ""}
                  onClick={() => setSide("buy")}
                  aria-pressed={side === "buy"}
                >
                  Buy / Long
                </button>
                <button
                  type="button"
                  className={side === "sell" ? "on-short" : ""}
                  onClick={() => setSide("sell")}
                  aria-pressed={side === "sell"}
                >
                  Sell / Short
                </button>
              </div>
            </div>

            <div className="field">
              <label htmlFor="stop-mode">Stop-loss</label>
              <div className="segmented">
                <button
                  id="stop-mode"
                  type="button"
                  className={stopMode === "points" ? "on-long" : ""}
                  onClick={() => setStopMode("points")}
                  aria-pressed={stopMode === "points"}
                >
                  Distance
                </button>
                <button
                  type="button"
                  className={stopMode === "price" ? "on-long" : ""}
                  onClick={() => setStopMode("price")}
                  aria-pressed={stopMode === "price"}
                >
                  Price
                </button>
              </div>
            </div>

            {stopMode === "points" ? (
              <Field
                label="Distance from entry (points)"
                hint={
                  spec.data
                    ? `1 point = ${spec.data.point}; broker minimum ${spec.data.stops_level_points} points`
                    : undefined
                }
              >
                <input
                  type="number"
                  value={stopPoints}
                  onChange={(e) => setStopPoints(e.target.value)}
                  min={1}
                  step={1}
                />
              </Field>
            ) : (
              <Field label="Stop price">
                <input
                  type="number"
                  value={stopPrice}
                  onChange={(e) => setStopPrice(e.target.value)}
                  step={spec.data?.tick_size ?? 0.00001}
                />
              </Field>
            )}

            <button className="btn btn-sm" type="button" onClick={() => void runScan()}>
              Show risk at nearby stops
            </button>

            <details className="mt">
              <summary className="small muted" style={{ cursor: "pointer" }}>
                Advanced
              </summary>
              <div className="mt">
                <Field
                  label="Entry price"
                  hint="Leave blank to use the live market price for this side."
                >
                  <input
                    type="number"
                    value={entryOverride}
                    onChange={(e) => setEntryOverride(e.target.value)}
                    step={spec.data?.tick_size ?? 0.00001}
                  />
                </Field>
                <Field
                  label="Lot size"
                  hint="Leave blank to use the Rule 2 allocation. A different value is blocked unless overrides are enabled."
                >
                  <input
                    type="number"
                    value={volumeOverride}
                    onChange={(e) => setVolumeOverride(e.target.value)}
                    step={spec.data?.volume_step ?? 0.01}
                    min={0}
                  />
                </Field>
                <Field label="Profit ladder">
                  <select
                    value={ladderPreset}
                    onChange={(e) => setLadderPreset(e.target.value as LadderPreset | "")}
                  >
                    <option value="">Profile default</option>
                    <option value="standard_1_2_3">Standard 1:1 / 1:2 / 1:3</option>
                    <option value="runner_1_2_3">Runner 1:1 / 1:2 / 1:3</option>
                  </select>
                </Field>
                <Field label="Comment" hint="Stamped on the MT5 order (31 characters)">
                  <input
                    type="text"
                    value={comment}
                    onChange={(e) => setComment(e.target.value)}
                    maxLength={48}
                  />
                </Field>
              </div>
            </details>
          </Card>

          {scan && (
            <Card title="Risk by stop distance" actions={
              <button className="btn btn-sm" onClick={() => setScan(null)}>
                Hide
              </button>
            }>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th className="num">Points</th>
                      <th className="num">Stop</th>
                      <th className="num">Loss</th>
                      <th className="num">% capital</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {scan.map((row) => (
                      <tr
                        key={row.stop_points}
                        className="clickable"
                        onClick={() => {
                          setStopMode("points");
                          setStopPoints(String(row.stop_points));
                        }}
                      >
                        <td className="num">{row.stop_points}</td>
                        <td className="num">{price(row.stop_price, spec.data?.digits ?? 5)}</td>
                        <td className="num">{money(row.loss, plan?.account_currency ?? "USD")}</td>
                        <td className="num">{percent(row.risk_pct)}</td>
                        <td>
                          <Badge tone={row.within_limit ? "ok" : "danger"}>
                            {row.within_limit ? "allowed" : "blocked"}
                          </Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="tiny faint mb-0">Pick a row to use that distance.</p>
            </Card>
          )}

          {spec.data && (
            <Card title={`${spec.data.name} contract`}>
              <dl className="kv">
                <dt>Description</dt>
                <dd className="small">{spec.data.description || spec.data.name}</dd>
                <dt>Lot range</dt>
                <dd>
                  {lots(spec.data.volume_min)} – {lots(spec.data.volume_max)} step{" "}
                  {spec.data.volume_step}
                </dd>
                <dt>Contract size</dt>
                <dd>{spec.data.contract_size.toLocaleString()}</dd>
                <dt>Value per 1.00 move / lot</dt>
                <dd>{money(spec.data.money_per_price_unit_per_lot, spec.data.currency_profit)}</dd>
                <dt>Minimum stop</dt>
                <dd>{points(spec.data.stops_level_points)}</dd>
                <dt>Tradable now</dt>
                <dd>{spec.data.trade_allowed ? "yes" : "no"}</dd>
              </dl>
            </Card>
          )}
        </div>

        {/* --------------------------------------------------- assessment */}
        <div className="stack">
          <ErrorBanner error={previewError} />
          <ErrorBanner error={submitError} />

          {result && !result.executed && (
            <Banner tone={result.approved ? "warn" : "error"} title={
              result.approved ? "Broker rejected the order" : "Entry blocked by the rules"
            }>
              {result.message}
            </Banner>
          )}

          {plan && rules ? (
            <>
              <PlanWarnings warnings={plan.warnings} />

              <Card>
                <div className="between">
                  <div>
                    <h2>
                      {plan.symbol} <span className={side === "buy" ? "long" : "short"}>
                        {side === "buy" ? "long" : "short"}
                      </span>{" "}
                      {lots(plan.volume)} lots
                    </h2>
                    <div className="small muted">
                      {price(plan.entry_price, plan.digits)} → stop{" "}
                      {price(plan.stop_loss, plan.digits)} ({points(plan.risk_points)}), targets{" "}
                      {plan.stages
                        .filter((s) => s.will_execute)
                        .map((s) => price(s.target_price, plan.digits))
                        .join(" / ")}
                    </div>
                  </div>
                  <Badge tone={rules.approved ? "ok" : "danger"}>
                    {rules.approved ? "rules passed" : "blocked"}
                  </Badge>
                </div>

                <div className="mt stack">
                  {canOverride && (
                    <label className="checkbox">
                      <input
                        type="checkbox"
                        checked={override}
                        onChange={(e) => setOverride(e.target.checked)}
                      />
                      <span>
                        Override the rules that permit it. Rule 1 (one trade per derivative) and
                        Rule 3 (2% risk ceiling) can never be overridden.
                      </span>
                    </label>
                  )}

                  <button
                    className="btn btn-primary btn-block"
                    disabled={blocked || submitting}
                    onClick={() => void submit()}
                  >
                    {submitting
                      ? "Sending to MT5..."
                      : blocked
                        ? "Blocked by the rules"
                        : `Place ${side} ${lots(plan.volume)} ${plan.symbol}`}
                  </button>

                  {blocked && (
                    <p className="tiny faint mb-0">{rules.summary}</p>
                  )}
                </div>
              </Card>

              <div className="grid grid-2">
                <RiskSummary plan={plan} />
                <RuleChecklist rules={rules} />
              </div>

              <LadderTable
                stages={plan.stages}
                currency={plan.account_currency}
                digits={plan.digits}
                ladder={assessment?.ladder}
              />
            </>
          ) : (
            <Card>
              <div className="empty">
                {previewing
                  ? "Calculating..."
                  : "Choose a derivative and a stop distance to see the full assessment."}
              </div>
            </Card>
          )}
        </div>
      </div>
    </>
  );
}

function SymbolOption({
  entry,
  selected,
  onSelect,
}: {
  entry: SymbolBrief;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      className={selected ? "pick selected" : "pick"}
      onClick={onSelect}
      aria-pressed={selected}
    >
      <span className="between">
        <span>
          <span className="strong mono">{entry.name}</span>{" "}
          <span className="tiny faint">{entry.group}</span>
          <div className="tiny faint">{entry.description}</div>
        </span>
        <span className="mono tiny">
          {entry.bid ? entry.bid.toFixed(entry.digits) : ""}
        </span>
      </span>
    </button>
  );
}
