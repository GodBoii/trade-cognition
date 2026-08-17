"use client";

/**
 * The pre-trade picture: risk summary, target ladder and the rule checklist.
 *
 * Shared by the trade ticket (before entry) and the trade detail page (after),
 * so the numbers a user approved are displayed the same way afterwards.
 */

import type { LadderInfo, RulesReport, StagePlan, TradePlan } from "@/lib/api/types";
import { lots, money, percent, points, price, ratio, signedMoney } from "@/lib/format";
import { Badge, Card } from "@/components/ui";

export function RiskSummary({ plan }: { plan: TradePlan }) {
  const currency = plan.account_currency;
  const usage = plan.max_risk_money > 0 ? (plan.max_loss / plan.max_risk_money) * 100 : 0;
  const meterClass = usage > 100 ? "over" : usage > 80 ? "warn" : "";

  return (
    <Card title="Risk at the stop">
      <dl className="kv">
        <dt>Entry ({plan.order_kind})</dt>
        <dd>{price(plan.entry_price, plan.digits)}</dd>

        <dt>Stop-loss</dt>
        <dd>
          {price(plan.stop_loss, plan.digits)}{" "}
          <span className="faint tiny">({points(plan.risk_points)})</span>
        </dd>

        <dt>Lot size</dt>
        <dd>
          {lots(plan.volume)}
          {!plan.volume_is_prescribed && (
            <span className="neg tiny"> vs {lots(plan.prescribed_volume)} allocated</span>
          )}
        </dd>

        <div className="divider" />

        <dt>Maximum loss</dt>
        <dd className="neg">{money(plan.max_loss, currency)}</dd>

        <dt>Capital at risk</dt>
        <dd className={usage > 100 ? "neg" : ""}>{percent(plan.risk_pct_of_capital)}</dd>

        <dt>
          Ceiling ({percent(plan.max_risk_pct, 2)} of {money(plan.capital, currency)})
        </dt>
        <dd>{money(plan.max_risk_money, currency)}</dd>

        <dt>Headroom</dt>
        <dd className={plan.risk_headroom < 0 ? "neg" : "pos"}>
          {signedMoney(plan.risk_headroom, currency)}
        </dd>

        <div className="divider" />

        <dt>Expected profit (plan complete)</dt>
        <dd className="pos">{money(plan.expected_profit, currency)}</dd>

        <dt>Reward / risk</dt>
        <dd>
          {ratio(plan.reward_risk_blended)}{" "}
          <span className="faint tiny">blended, {ratio(plan.reward_risk_final)} final</span>
        </dd>

        <dt>Required margin</dt>
        <dd>
          {money(plan.required_margin, currency)}{" "}
          <span className="faint tiny">({percent(plan.margin_pct_of_free_margin, 1)} of free)</span>
        </dd>

        <dt>Value per point</dt>
        <dd>{money(plan.money_per_point, currency)}</dd>

        <dt>Spread cost</dt>
        <dd>
          {money(plan.spread_cost, currency)}{" "}
          <span className="faint tiny">({points(plan.spread_points)})</span>
        </dd>
      </dl>

      <div className="mt">
        <div className="between tiny muted">
          <span>Risk budget used</span>
          <span className="mono">{percent(usage, 0)}</span>
        </div>
        <div className="meter" title={`${usage.toFixed(0)}% of the configured risk ceiling`}>
          <div
            className={`meter-fill ${meterClass}`}
            style={{ width: `${Math.min(usage, 100)}%` }}
          />
        </div>
        {plan.max_stop_points > 0 && (
          <p className="tiny faint mt mb-0">
            At {lots(plan.volume)} lots the stop may sit no further than{" "}
            {points(plan.max_stop_points)} away ({price(plan.max_stop_price, plan.digits)}) before
            Rule 3 blocks the entry.
          </p>
        )}
      </div>
    </Card>
  );
}

export function LadderTable({
  stages,
  currency,
  digits,
  ladder,
}: {
  stages: StagePlan[];
  currency: string;
  digits: number;
  ladder?: LadderInfo;
}) {
  return (
    <Card
      title="Profit ladder"
      hint={ladder?.description}
      actions={ladder ? <Badge tone="info">{ladder.label}</Badge> : null}
    >
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Rung</th>
              <th className="num">R</th>
              <th className="num">Target</th>
              <th className="num">Close</th>
              <th className="num">Profit</th>
              <th className="num">Running</th>
              <th className="num">Stop after</th>
              <th className="num">Worst case</th>
            </tr>
          </thead>
          <tbody>
            {stages.map((stage) => (
              <tr key={stage.key} className={stage.will_execute ? undefined : "dim"}>
                <td>
                  <span className="strong">{stage.key}</span>
                  {!stage.will_execute && <span className="faint tiny"> no volume</span>}
                </td>
                <td className="num">1:{stage.r_multiple.toFixed(0)}</td>
                <td className="num">{price(stage.target_price, digits)}</td>
                <td className="num">{stage.volume > 0 ? lots(stage.volume) : "-"}</td>
                <td className="num pos">
                  {stage.money_profit > 0 ? money(stage.money_profit, currency) : "-"}
                </td>
                <td className="num">{money(stage.cumulative_money, currency)}</td>
                <td className="num">
                  {stage.sl_after !== null ? price(stage.sl_after, digits) : "-"}
                </td>
                <td className={`num ${stage.locked_in_money >= 0 ? "pos" : "neg"}`}>
                  {signedMoney(stage.locked_in_money, currency)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="tiny faint mt mb-0">
        <strong>Worst case</strong> is what the account keeps if the newly placed stop is hit
        immediately after that rung: it is how the 1:1 exit turns an open risk into a locked-in
        gain.
      </p>
    </Card>
  );
}

export function RuleChecklist({ rules }: { rules: RulesReport }) {
  const blocking = rules.checks.filter((c) => !c.passed && c.severity === "block");
  const others = rules.checks.filter((c) => !(blocking.includes(c)));

  return (
    <Card
      title="Rule validation"
      actions={
        <Badge tone={rules.approved ? "ok" : "danger"}>
          {rules.approved ? "Approved" : `${blocking.length} blocking`}
        </Badge>
      }
    >
      {[...blocking, ...others].map((check) => (
        <div className="rule" key={check.code}>
          <div
            className={`rule-icon ${check.passed ? "pos" : check.severity === "block" ? "neg" : "muted"}`}
            aria-hidden="true"
          >
            {check.passed ? "\u2713" : check.severity === "block" ? "\u2717" : "!"}
          </div>
          <div className="rule-body">
            <div className="rule-name">
              {check.rule}
              {check.overridable && !check.passed && (
                <span className="faint tiny"> overridable</span>
              )}
            </div>
            <div className="rule-msg">{check.message}</div>
            <div className="rule-code">{check.code}</div>
          </div>
        </div>
      ))}
    </Card>
  );
}

export function PlanWarnings({ warnings }: { warnings: string[] }) {
  if (warnings.length === 0) return null;
  return (
    <div className="banner banner-warn">
      <div className="banner-title">
        {warnings.length === 1 ? "One thing to note" : `${warnings.length} things to note`}
      </div>
      <ul style={{ margin: "0 0 0 18px", padding: 0 }}>
        {warnings.map((warning) => (
          <li key={warning}>{warning}</li>
        ))}
      </ul>
    </div>
  );
}
