"""The user's risk profile - the configuration the rules engine enforces."""

from __future__ import annotations

from dataclasses import dataclass, replace

from .enums import CapitalBasis, LadderPreset, LotRuleMode
from .market import AccountSnapshot, SymbolSpec
from .quant import round_volume


@dataclass(frozen=True, slots=True)
class RiskProfile:
    """Per-user risk configuration.

    Defaults encode the house rules from the specification:
    ``0.02`` lots per ``$1,000`` of capital and a hard ``2%`` ceiling on the
    monetary loss at the proposed stop-loss.
    """

    # --- Rule 2: capital -> lot size --------------------------------------
    lots_per_1000: float = 0.02
    lot_rule_mode: LotRuleMode = LotRuleMode.STRICT

    # --- Rule 3: risk ceiling ---------------------------------------------
    max_risk_pct: float = 2.0

    # --- What counts as trading capital -----------------------------------
    capital_basis: CapitalBasis = CapitalBasis.BALANCE
    fixed_capital: float = 0.0

    # --- Profit taking -----------------------------------------------------
    ladder_preset: LadderPreset = LadderPreset.RUNNER_1_2_3

    # --- Additional portfolio guards (0 / False disables the guard) --------
    max_concurrent_positions: int = 0
    max_daily_loss_pct: float = 0.0
    margin_utilisation_cap_pct: float = 50.0
    require_stop_loss: bool = True
    min_reward_risk: float = 1.0
    allow_manual_override: bool = False

    # ------------------------------------------------------------------ money
    def capital(self, account: AccountSnapshot) -> float:
        """Trading capital for this profile, given an account snapshot."""
        return account.capital(self.capital_basis.value, self.fixed_capital)

    def max_risk_money(self, capital: float) -> float:
        """Rule 3 ceiling in account currency."""
        return capital * (self.max_risk_pct / 100.0)

    def margin_budget(self, account: AccountSnapshot) -> float:
        """Largest margin a single new trade may consume."""
        cap = self.margin_utilisation_cap_pct
        if cap <= 0:
            return float("inf")
        return account.margin_free * (cap / 100.0)

    # ------------------------------------------------------------------- lots
    def raw_prescribed_volume(self, capital: float) -> float:
        """Rule 2 volume before broker quantisation.

        ``lots_per_1000`` lots for every ``1,000`` units of capital::

            volume = capital / 1000 * lots_per_1000
        """
        if capital <= 0:
            return 0.0
        return (capital / 1000.0) * self.lots_per_1000

    def prescribed_volume(self, capital: float, spec: SymbolSpec) -> float:
        """Rule 2 volume, floored onto the symbol's lot grid and clamped.

        Flooring (never rounding up) guarantees the allocation rule is a
        ceiling: the platform will not silently size a position larger than the
        capital formula permits.
        """
        raw = self.raw_prescribed_volume(capital)
        if raw <= 0:
            return 0.0
        stepped = round_volume(raw, spec.volume_step, "floor")
        if stepped < spec.volume_min:
            # Below the tradable minimum: report 0 so the rules engine can
            # explain that the account is too small for this symbol.
            return 0.0
        return round_volume(min(stepped, spec.volume_max), spec.volume_step, "floor")

    def with_overrides(self, **changes: object) -> RiskProfile:
        return replace(self, **changes)  # type: ignore[arg-type]


DEFAULT_PROFILE = RiskProfile()
