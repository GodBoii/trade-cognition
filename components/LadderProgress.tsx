"use client";

import type { Trade, TradeStage } from "@/lib/api/types";
import { lots, signedMoney } from "@/lib/format";

/**
 * Compact view of where a position sits on its 1:1 / 1:2 / 1:3 ladder.
 * Filled rungs show what they booked; pending rungs show what they will close.
 */
export function LadderProgress({ trade }: { trade: Trade }) {
  if (trade.stages.length === 0) {
    return <span className="faint tiny">no ladder</span>;
  }

  return (
    <div className="ladder">
      {trade.stages.map((stage) => (
        <div key={stage.stage_key} className={`rung ${rungClass(stage)}`}>
          <div className="rung-key">{stage.stage_key}</div>
          <div className="rung-detail">
            {stage.status === "filled"
              ? signedMoney(stage.realised_pl, trade.account_currency)
              : stage.planned_volume > 0
                ? lots(stage.planned_volume)
                : "-"}
          </div>
        </div>
      ))}
    </div>
  );
}

function rungClass(stage: TradeStage): string {
  switch (stage.status) {
    case "filled":
      return "rung-filled";
    case "skipped":
      return "rung-skipped";
    case "failed":
      return "rung-failed";
    default:
      return "";
  }
}
