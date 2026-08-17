"""Ladder volume allocation across the broker's lot grid."""

from __future__ import annotations

import pytest

from app.domain.enums import LadderPreset, SlAction
from app.domain.ladder import (
    RUNNER_1_2_3,
    STANDARD_1_2_3,
    allocate_stage_volumes,
    get_ladder,
)


def alloc(total: float, ladder=STANDARD_1_2_3, step: float = 0.01, minimum: float = 0.01):
    return allocate_stage_volumes(total, ladder, step, minimum)


def test_standard_splits_fifty_fifty_when_the_grid_allows():
    volumes, warnings = alloc(0.20)
    assert volumes == [0.1, 0.1, 0.0]
    assert warnings == []


def test_odd_lot_count_leaves_a_residual_for_the_final_rung():
    # 0.03 lots cannot be halved twice, so 1:3 becomes meaningful.
    volumes, _ = alloc(0.03)
    assert volumes == [0.01, 0.01, 0.01]
    assert sum(volumes) == pytest.approx(0.03)


def test_minimum_size_position_runs_as_a_single_tranche():
    volumes, warnings = alloc(0.01)
    assert volumes == [0.0, 0.0, 0.01]
    assert any("cannot be scaled out" in w for w in warnings)


def test_never_strands_volume_below_the_minimum_lot():
    # Broker with a 0.01 step but a 0.05 minimum: halving 0.11 twice would leave
    # a 0.01 residual that could never be closed, so TP2 takes the lot instead.
    volumes, warnings = alloc(0.11, step=0.01, minimum=0.05)

    assert sum(volumes) == pytest.approx(0.11)
    assert all(v == 0 or v >= 0.05 for v in volumes)
    assert volumes == [0.05, 0.06, 0.0]
    assert any("strand" in w for w in warnings)  # the deviation is explained, not silent


def test_coarse_grid_conserves_volume_and_respects_the_minimum():
    volumes, _ = alloc(0.3, step=0.1, minimum=0.1)
    assert volumes == [0.1, 0.1, 0.1]
    assert all(v == 0 or v >= 0.1 for v in volumes)


def test_allocation_always_conserves_total_volume():
    for total in (0.01, 0.02, 0.03, 0.05, 0.07, 0.2, 1.37, 12.5):
        volumes, _ = alloc(total)
        assert sum(volumes) == pytest.approx(total, abs=1e-9), total


def test_runner_preset_keeps_a_tranche_for_the_final_target():
    volumes, _ = alloc(0.4, ladder=RUNNER_1_2_3)
    assert volumes == [0.2, 0.1, 0.1]


def test_stage_definitions_match_the_house_rules():
    tp1, tp2, tp3 = STANDARD_1_2_3.stages
    assert (tp1.r_multiple, tp1.close_fraction) == (1.0, 0.50)
    assert tp1.sl_action is SlAction.HALVE_ORIGINAL_DISTANCE
    assert (tp2.r_multiple, tp2.close_fraction) == (2.0, 0.50)
    assert tp2.sl_action is SlAction.MOVE_TO_TP1
    assert tp3.r_multiple == 3.0
    assert tp3.final


def test_get_ladder_falls_back_to_the_runner_default():
    assert get_ladder(None) is RUNNER_1_2_3
    assert get_ladder("nonsense") is RUNNER_1_2_3
    assert get_ladder(LadderPreset.RUNNER_1_2_3) is RUNNER_1_2_3
    assert get_ladder("runner_1_2_3") is RUNNER_1_2_3
