"use client";

import { useEffect, useState } from "react";

import { Badge, Banner, Card, ErrorBanner, Field, PageHead, Spinner } from "@/components/ui";
import { saveTradingRules } from "@/lib/supabase/data";
import type { UserTradingRules } from "@/lib/supabase/types";
import { useAuth } from "@/state/auth";
import { useTrading } from "@/state/trading";

export default function RulesView() {
  const { user } = useAuth();
  const { rules, loading, error: loadError, refresh } = useTrading();
  const [draft, setDraft] = useState<UserTradingRules | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (rules) setDraft(rules);
  }, [rules]);

  const set = <K extends keyof UserTradingRules>(key: K, value: UserTradingRules[K]) => {
    if (!draft) return;
    setDraft({ ...draft, [key]: value });
    setSaved(false);
  };

  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!user || !draft) return;
    setSaving(true);
    setError(null);
    try {
      const next = await saveTradingRules(user.id, {
        capital_basis: draft.capital_basis,
        fixed_capital: draft.fixed_capital,
        max_concurrent_positions: draft.max_concurrent_positions,
        max_daily_loss_pct: draft.max_daily_loss_pct,
        margin_utilisation_cap_pct: draft.margin_utilisation_cap_pct,
        min_reward_risk: draft.min_reward_risk,
      });
      setDraft(next);
      await refresh();
      setSaved(true);
    } catch (cause) {
      setError(cause);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="page-enter">
      <PageHead
        title="Trading rules"
        subtitle="Supabase stores this strategy; the local worker revalidates it with fresh MT5 data immediately before any order reaches the broker."
        actions={<Badge tone="info">worker enforced</Badge>}
      />

      <ErrorBanner error={loadError} />
      <ErrorBanner error={error} />
      {saved && <Banner tone="ok">Rules saved in Supabase for the next trade.</Banner>}

      {loading && !draft ? (
        <Spinner label="loading rules from Supabase" />
      ) : !draft ? (
        <Card title="Rules are not installed">
          <p className="muted mb-0">
            Run <code>supabase/002_async_trade_queue.sql</code> in the Supabase SQL Editor, then
            refresh this page. The website remains usable while the database setup is incomplete.
          </p>
        </Card>
      ) : (
        <form onSubmit={save}>
          <div className="grid grid-2">
            <Card title="Rule 1 — one active trade per derivative">
              <p className="muted">
                A user may have only one queued, validating, submitted, or open trade for the same
                symbol. The database unique index and the worker both enforce this rule.
              </p>
              <Badge tone="ok">always enabled · not overridable</Badge>
            </Card>

            <Card title="Rule 2 — strict lot allocation">
              <p className="muted">
                Position allocation is exactly <strong>0.02 lots per 1,000</strong> units of the
                selected trading capital, then rounded down to the broker&apos;s valid lot step.
              </p>
              <Badge tone="ok">0.02 / 1,000 · not overridable</Badge>
              <div className="mt">
                <Field label="Capital basis" hint="Balance figure used by the allocation and risk cap">
                  <select
                    value={draft.capital_basis}
                    onChange={(event) =>
                      set(
                        "capital_basis",
                        event.target.value as UserTradingRules["capital_basis"],
                      )
                    }
                  >
                    <option value="balance">Balance</option>
                    <option value="equity">Equity (includes floating P/L)</option>
                    <option value="fixed">Fixed allocation</option>
                  </select>
                </Field>
                {draft.capital_basis === "fixed" && (
                  <Field label="Fixed trading capital">
                    <input
                      type="number"
                      min={1}
                      step={100}
                      value={draft.fixed_capital}
                      onChange={(event) => set("fixed_capital", Number(event.target.value))}
                      required
                    />
                  </Field>
                )}
              </div>
            </Card>

            <Card title="Rule 3 — maximum stop-loss risk">
              <p className="muted">
                The calculated loss at the proposed stop may never exceed <strong>2%</strong> of
                selected trading capital. Exact risk uses MT5 tick value and the current quote.
              </p>
              <Badge tone="ok">2% hard ceiling · not overridable</Badge>
              <div className="mt">
                <Field
                  label="Minimum final reward-to-risk"
                  hint="A higher value may reject more trades; 0 disables this extra guard"
                >
                  <input
                    type="number"
                    min={0}
                    max={100}
                    step={0.1}
                    value={draft.min_reward_risk}
                    onChange={(event) => set("min_reward_risk", Number(event.target.value))}
                  />
                </Field>
              </div>
            </Card>

            <Card title="Profit ladder — 1R / 2R / 3R">
              <ol className="small muted" style={{ margin: 0, paddingLeft: 18 }}>
                <li>TP1 at 1R: close 50%; move SL to half the original stop distance.</li>
                <li>TP2 at 2R: close 25%; move SL to the TP1 price.</li>
                <li>TP3 at 3R: close the final 25%.</li>
              </ol>
              <div className="mt">
                <Badge tone="ok">runner_1_2_3 · 50% / 25% / 25%</Badge>
              </div>
            </Card>

            <Card title="Additional portfolio guards" hint="0 disables an optional guard">
              <Field label="Maximum concurrent positions" hint="Across different derivatives">
                <input
                  type="number"
                  min={0}
                  max={100}
                  value={draft.max_concurrent_positions}
                  onChange={(event) =>
                    set("max_concurrent_positions", Number(event.target.value))
                  }
                />
              </Field>
              <Field label="Daily realised-loss limit (% of capital)">
                <input
                  type="number"
                  min={0}
                  max={100}
                  step={0.5}
                  value={draft.max_daily_loss_pct}
                  onChange={(event) => set("max_daily_loss_pct", Number(event.target.value))}
                />
              </Field>
              <Field label="Margin cap (% of currently free margin)">
                <input
                  type="number"
                  min={0}
                  max={100}
                  step={5}
                  value={draft.margin_utilisation_cap_pct}
                  onChange={(event) =>
                    set("margin_utilisation_cap_pct", Number(event.target.value))
                  }
                />
              </Field>
            </Card>

            <Card title="Offline behavior">
              <p className="muted">
                New instructions expire after five minutes by default and can never be scheduled
                more than fifteen minutes ahead. An offline worker will not place a stale market
                order when it returns.
              </p>
              <p className="tiny faint mb-0">
                Existing broker-side hard SL/TP remains active. Custom partial exits and stop
                movements require the worker or an Expert Advisor to be online.
              </p>
            </Card>
          </div>

          <div className="mt btn-group">
            <button className="btn btn-primary" type="submit" disabled={saving}>
              {saving ? (
                <span className="t-shimmer" data-text="Saving to Supabase...">Saving to Supabase...</span>
              ) : (
                "Save configurable guards"
              )}
            </button>
            <button
              className="btn"
              type="button"
              onClick={() => rules && setDraft(rules)}
              disabled={saving}
            >
              Discard changes
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
