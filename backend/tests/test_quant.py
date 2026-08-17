"""Quantisation: the arithmetic that keeps the broker from rejecting orders."""

from __future__ import annotations

import pytest

from app.domain.quant import (
    decimals_of,
    is_multiple_of,
    pct,
    quantise,
    round_price,
    round_volume,
    safe_div,
)


@pytest.mark.parametrize(
    ("value", "step", "mode", "expected"),
    [
        (0.234, 0.01, "floor", 0.23),
        (0.234, 0.01, "ceil", 0.24),
        (0.235, 0.01, "nearest", 0.24),
        # 0.1 + 0.2 in binary floats is 0.30000000000000004; flooring naively
        # would drop a whole lot step.
        (0.1 + 0.2, 0.01, "floor", 0.30),
        (0.02 * 3, 0.01, "floor", 0.06),
        (7.0, 0.5, "floor", 7.0),
        (0.05, 0.1, "floor", 0.0),
        (1.0, 0.0, "floor", 1.0),
    ],
)
def test_quantise(value, step, mode, expected):
    assert quantise(value, step, mode) == pytest.approx(expected, abs=1e-12)


def test_round_volume_keeps_clean_decimals():
    assert round_volume(0.30000000000000004, 0.01, "floor") == 0.3
    assert round_volume(0.19999999999999998, 0.01, "floor") == 0.2


def test_round_price_snaps_to_tick_and_digits():
    assert round_price(1.234567, 5, 1e-5) == 1.23457
    assert round_price(2350.004, 2, 0.01) == 2350.0
    # A 0.25-tick instrument may only quote quarter points.
    assert round_price(5401.13, 2, 0.25) == 5401.25


def test_decimals_of():
    assert decimals_of(0.01) == 2
    assert decimals_of(0.1) == 1
    assert decimals_of(1) == 0
    assert decimals_of(0.001) == 3


def test_is_multiple_of():
    assert is_multiple_of(0.3, 0.01)
    assert is_multiple_of(0.30000000000000004, 0.01)
    assert not is_multiple_of(0.15, 0.1)
    assert is_multiple_of(5.0, 0.0)


def test_safe_div_and_pct_never_raise():
    assert safe_div(1.0, 0.0) == 0.0
    assert safe_div(1.0, 0.0, default=-1.0) == -1.0
    assert pct(50, 200) == 25.0
    assert pct(50, 0) == 0.0
