"""Profit-taking ladders (the 1:1 -> 1:2 -> 1:3 progression).

A ladder is declarative data: an ordered list of stages, each with an R
multiple, the fraction of the **original** position to close there, and the
stop-loss action to apply afterwards.  The monitor walks this list; it contains
no hard-coded TP logic, so changing house rules is a configuration change.

Two presets ship with the platform - see ``docs/04-position-management.md`` and
ADR-0003 for why both exist.
"""

from __future__ import annotations

from dataclasses import dataclass

from .enums import LadderPreset, SlAction
from .quant import round_volume

TP1 = "TP1"
TP2 = "TP2"
TP3 = "TP3"


@dataclass(frozen=True, slots=True)
class LadderStage:
    """One rung of the ladder."""

    key: str
    r_multiple: float
    #: Fraction of the *original* position volume to close at this stage.
    close_fraction: float
    sl_action: SlAction = SlAction.NONE
    #: The final stage always closes whatever volume is left.
    final: bool = False
    note: str = ""


@dataclass(frozen=True, slots=True)
class Ladder:
    preset: LadderPreset
    label: str
    description: str
    stages: tuple[LadderStage, ...]

    def stage(self, key: str) -> LadderStage:
        for stage in self.stages:
            if stage.key == key:
                return stage
        raise KeyError(f"unknown ladder stage {key!r}")

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(stage.key for stage in self.stages)

    @property
    def max_r(self) -> float:
        return max(stage.r_multiple for stage in self.stages)


STANDARD_1_2_3 = Ladder(
    preset=LadderPreset.STANDARD_1_2_3,
    label="Standard 1:1 / 1:2 / 1:3",
    description=(
        "House rule as written: take 50% off at 1R and tighten the stop to half "
        "the original distance; close the remaining 50% at 2R while the stop is "
        "moved to the TP1 level. The 1:3 rung only executes on volume that "
        "survives lot-step rounding at TP2."
    ),
    stages=(
        LadderStage(
            key=TP1,
            r_multiple=1.0,
            close_fraction=0.50,
            sl_action=SlAction.HALVE_ORIGINAL_DISTANCE,
            note="Close 50% of the original position; SL to 50% of original SL distance.",
        ),
        LadderStage(
            key=TP2,
            r_multiple=2.0,
            close_fraction=0.50,
            sl_action=SlAction.MOVE_TO_TP1,
            note="Close the remaining 50% of the original position; SL to the TP1 level.",
        ),
        LadderStage(
            key=TP3,
            r_multiple=3.0,
            close_fraction=1.0,
            sl_action=SlAction.MOVE_TO_PREVIOUS_TARGET,
            final=True,
            note="Final 1:3 exit for any residual volume.",
        ),
    ),
)

RUNNER_1_2_3 = Ladder(
    preset=LadderPreset.RUNNER_1_2_3,
    label="Runner 1:1 / 1:2 / 1:3",
    description=(
        "Same stop management, but only 25% is closed at 2R so a 25% runner is "
        "carried to the 1:3 target with the stop parked at TP1."
    ),
    stages=(
        LadderStage(
            key=TP1,
            r_multiple=1.0,
            close_fraction=0.50,
            sl_action=SlAction.HALVE_ORIGINAL_DISTANCE,
            note="Close 50% of the original position; SL to 50% of original SL distance.",
        ),
        LadderStage(
            key=TP2,
            r_multiple=2.0,
            close_fraction=0.25,
            sl_action=SlAction.MOVE_TO_TP1,
            note="Close 25% of the original position; SL to the TP1 level.",
        ),
        LadderStage(
            key=TP3,
            r_multiple=3.0,
            close_fraction=0.25,
            sl_action=SlAction.MOVE_TO_PREVIOUS_TARGET,
            final=True,
            note="Close the runner at the 1:3 target.",
        ),
    ),
)

LADDERS: dict[LadderPreset, Ladder] = {
    LadderPreset.STANDARD_1_2_3: STANDARD_1_2_3,
    LadderPreset.RUNNER_1_2_3: RUNNER_1_2_3,
}

# The product requires a meaningful final 1:3 target.  The written "remaining
# 50% at TP2" wording would exhaust the position before TP3, so the coherent
# default is 50% / 25% / 25%.  The legacy standard preset remains available for
# users who intentionally want to be fully out by TP2.
DEFAULT_LADDER = RUNNER_1_2_3


def get_ladder(preset: LadderPreset | str | None) -> Ladder:
    """Resolve a ladder by preset, falling back to the default."""
    if preset is None:
        return DEFAULT_LADDER
    if isinstance(preset, str):
        try:
            preset = LadderPreset(preset)
        except ValueError:
            return DEFAULT_LADDER
    return LADDERS.get(preset, DEFAULT_LADDER)


def allocate_stage_volumes(
    total_volume: float,
    ladder: Ladder,
    volume_step: float,
    volume_min: float,
) -> tuple[list[float], list[str]]:
    """Split ``total_volume`` across ladder stages, respecting the lot grid.

    Partial closes must land on the broker's ``volume_step`` grid *and* must
    never leave an untradable remainder (a residual below ``volume_min`` cannot
    be closed later, stranding the position).  The algorithm therefore:

    1. floors each non-final stage onto the volume grid;
    2. zeroes a stage whose share rounds below ``volume_min``;
    3. promotes a stage to a full close when the remainder it would leave is
       non-zero but below ``volume_min``;
    4. gives the final stage everything that is left.

    Returns the per-stage volumes plus human readable warnings describing any
    deviation from the nominal fractions.
    """
    step = volume_step if volume_step and volume_step > 0 else 0.01
    vmin = volume_min if volume_min and volume_min > 0 else step

    volumes: list[float] = []
    warnings: list[str] = []
    remaining = round_volume(total_volume, step, "nearest")
    stage_count = len(ladder.stages)

    for index, stage in enumerate(ladder.stages):
        is_final = stage.final or index == stage_count - 1

        if remaining <= 0:
            volumes.append(0.0)
            continue

        if is_final:
            volumes.append(remaining)
            remaining = 0.0
            continue

        share = round_volume(total_volume * stage.close_fraction, step, "floor")

        if share < vmin:
            volumes.append(0.0)
            warnings.append(
                f"{stage.key}: {stage.close_fraction:.0%} of {total_volume:g} lots is below the "
                f"{vmin:g} minimum lot, so no volume is scheduled there."
            )
            continue

        share = min(share, remaining)
        leftover = round_volume(remaining - share, step, "nearest")

        if 0 < leftover < vmin:
            warnings.append(
                f"{stage.key}: closing {share:g} lots would strand {leftover:g} lots below the "
                f"{vmin:g} minimum, so the full {remaining:g} lots exit here instead."
            )
            share = remaining
            leftover = 0.0

        volumes.append(share)
        remaining = leftover

    if remaining > 0:  # pragma: no cover - final stage always drains
        volumes[-1] = round_volume(volumes[-1] + remaining, step, "nearest")

    scheduled = [v for v in volumes[:-1] if v > 0]
    if not scheduled:
        warnings.append(
            f"Position of {total_volume:g} lots cannot be scaled out on a {step:g} lot grid; "
            f"it will be held as a single tranche to the {ladder.stages[-1].key} target."
        )

    return volumes, warnings
