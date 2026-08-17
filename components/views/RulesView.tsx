"use client";

import { useEffect, useState } from "react";

import { api } from "@/lib/api/client";
import type { RiskProfile } from "@/lib/api/types";
import { Badge, Banner, Card, ErrorBanner, Field, Spinner } from "@/components/ui";
import { lots, money } from "@/lib/format";
import { useAsync } from "@/lib/useAsync";
import { useAuth } from "@/state/auth";

export default function RulesView() {
  const { account } = useAuth();
  const loaded = useAsync(() => api.profile(), []);
  const ladders = useAsync(() => api.ladders(), []);

  const [draft, setDraft] = useState<RiskProfile | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (loaded.data) setDraft(loaded.data);
  }, [loaded.data]);

  if (loaded.loading || !draft) return <Spinner label="loading risk profile" />;

  const set = <K extends keyof RiskProfile>(key: K, value: RiskProfile[K]) => {
    setDraft({ ...draft, [key]: value });
    setSaved(false);
  };

  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const next = await api.saveProfile(draft);
      setDraft(next);
      setSaved(true);
    } catch (cause) {
      setError(cause);
    } finally {
      setSaving(false);
    }
  };

  const capital = account?.last_balance ?? 0;
  const exampleLots = (capital / 1000) * draft.lots_per_1000;
  const exampleRisk = capital * (draft.max_risk_pct / 100);
  const currency = account?.currency ?? "USD";

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Trading rules</h1>
          <p>
            These settings are what the engine enforces on every entry. Rule 1 (one active trade per
            derivative) is structural and has no configuration.
          </p>
        </div>
      </div>

      <ErrorBanner error={loaded.error} />
      <ErrorBanner error={error} />
      {saved && <Banner tone="ok">Risk profile saved. It applies to the next entry.</Banner>}

      <form onSubmit={save}>
        <div className="grid grid-2">
          <Card title="Rule 2 — lot allocation from capital">
            <Field
              label="Lots per 1,000 of capital"
              hint="House standard is 0.02. Values above 0.10 are refused."
            >
              <input
                type="number"
                step={0.001}
                min={0.001}
                max={0.1}
                value={draft.lots_per_1000}
                onChange={(e) => set("lots_per_1000", Number(e.target.value))}
                required
              />
            </Field>

            <Field
              label="Enforcement"
              hint="Strict requires the exact allocation; ceiling allows smaller sizes."
            >
              <select
                value={draft.lot_rule_mode}
                onChange={(e) => set("lot_rule_mode", e.target.value as RiskProfile["lot_rule_mode"])}
              >
                <option value="strict">Strict — must match the allocation</option>
                <option value="max">Ceiling — may be smaller</option>
              </select>
            </Field>

            <Field label="Capital basis" hint="Which account figure the allocation is computed from">
              <select
                value={draft.capital_basis}
                onChange={(e) => set("capital_basis", e.target.value as RiskProfile["capital_basis"])}
              >
                <option value="balance">Balance</option>
                <option value="equity">Equity (includes floating P/L)</option>
                <option value="fixed">Fixed amount</option>
              </select>
            </Field>

            {draft.capital_basis === "fixed" && (
              <Field label="Fixed capital" hint="Useful when only part of the account is allocated">
                <input
                  type="number"
                  min={1}
                  step={100}
                  value={draft.fixed_capital}
                  onChange={(e) => set("fixed_capital", Number(e.target.value))}
                />
              </Field>
            )}

            {capital > 0 && (
              <p className="tiny faint mb-0">
                On the current balance of {money(capital, currency)} this prescribes{" "}
                <strong>{lots(exampleLots)} lots</strong> per entry, before the symbol lot step is
                applied.
              </p>
            )}
          </Card>

          <Card title="Rule 3 — maximum loss at the stop">
            <Field
              label="Maximum risk per trade (% of capital)"
              hint="House standard is 2%. This can never be overridden at entry."
            >
              <input
                type="number"
                step={0.1}
                min={0.1}
                max={20}
                value={draft.max_risk_pct}
                onChange={(e) => set("max_risk_pct", Number(e.target.value))}
                required
              />
            </Field>

            <Field
              label="Minimum reward-to-risk"
              hint="Blocks entries whose final target is below this multiple. 0 disables."
            >
              <input
                type="number"
                step={0.1}
                min={0}
                value={draft.min_reward_risk}
                onChange={(e) => set("min_reward_risk", Number(e.target.value))}
              />
            </Field>

            {capital > 0 && (
              <p className="tiny faint mb-0">
                A stopped-out trade may cost at most{" "}
                <strong>{money(exampleRisk, currency)}</strong>.
              </p>
            )}
          </Card>

          <Card title="Profit taking">
            <Field label="Ladder" hint="Applies to new entries; open trades keep the ladder they started with.">
              <select
                value={draft.ladder_preset}
                onChange={(e) => set("ladder_preset", e.target.value as RiskProfile["ladder_preset"])}
              >
                <option value="standard_1_2_3">Standard 1:1 / 1:2 / 1:3</option>
                <option value="runner_1_2_3">Runner 1:1 / 1:2 / 1:3</option>
              </select>
            </Field>

            {ladders.data?.map((ladder) => (
              <div key={ladder.preset} className="mt">
                <div className="inline">
                  <strong className="small">{ladder.label}</strong>
                  {ladder.preset === draft.ladder_preset && <Badge tone="info">selected</Badge>}
                </div>
                <p className="tiny muted">{ladder.description}</p>
                <ul className="tiny faint" style={{ margin: 0, paddingLeft: 18 }}>
                  {ladder.stages.map((stage) => (
                    <li key={stage.key}>
                      <strong>{stage.key}</strong> at 1:{stage.r_multiple.toFixed(0)} — close{" "}
                      {(stage.close_fraction * 100).toFixed(0)}%, stop{" "}
                      {stage.sl_action.replace(/_/g, " ")}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </Card>

          <Card title="Portfolio guards" hint="Set to 0 to disable a guard">
            <Field
              label="Maximum concurrent positions"
              hint="Across all derivatives. 0 means unlimited."
            >
              <input
                type="number"
                min={0}
                max={100}
                value={draft.max_concurrent_positions}
                onChange={(e) => set("max_concurrent_positions", Number(e.target.value))}
              />
            </Field>

            <Field
              label="Daily loss limit (% of capital)"
              hint="Once realised losses reach this, new entries are blocked until tomorrow."
            >
              <input
                type="number"
                min={0}
                max={100}
                step={0.5}
                value={draft.max_daily_loss_pct}
                onChange={(e) => set("max_daily_loss_pct", Number(e.target.value))}
              />
            </Field>

            <Field
              label="Margin utilisation cap (% of free margin)"
              hint="Refuses entries whose margin requirement exceeds this share of free margin."
            >
              <input
                type="number"
                min={0}
                max={100}
                step={5}
                value={draft.margin_utilisation_cap_pct}
                onChange={(e) => set("margin_utilisation_cap_pct", Number(e.target.value))}
              />
            </Field>

            <label className="checkbox">
              <input
                type="checkbox"
                checked={draft.allow_manual_override}
                onChange={(e) => set("allow_manual_override", e.target.checked)}
              />
              <span>
                Allow manual override of overridable rules (lot allocation, margin cap, minimum
                reward-to-risk). Rule 1 and Rule 3 are never overridable.
              </span>
            </label>
          </Card>
        </div>

        <div className="mt btn-group">
          <button className="btn btn-primary" type="submit" disabled={saving}>
            {saving ? "Saving..." : "Save risk profile"}
          </button>
          <button
            className="btn"
            type="button"
            onClick={() => loaded.data && setDraft(loaded.data)}
            disabled={saving}
          >
            Discard changes
          </button>
        </div>
      </form>
    </>
  );
}
