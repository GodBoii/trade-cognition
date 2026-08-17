"""Numeric helpers for price and volume quantisation.

Broker instruments are quantised: prices move in ``tick_size`` increments and
volumes in ``volume_step`` increments.  Doing that arithmetic in binary floats
produces artefacts (``0.1 + 0.2 == 0.30000000000000004``) which the MT5 server
rejects as *invalid volume* or *invalid price*.  Every quantisation therefore
goes through :class:`~decimal.Decimal` and is returned as a clean float.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Literal

RoundMode = Literal["floor", "ceil", "nearest"]

_MODE_MAP = {
    "floor": ROUND_FLOOR,
    "ceil": ROUND_CEILING,
    "nearest": ROUND_HALF_UP,
}


def dec(value: float | int | str | Decimal) -> Decimal:
    """Convert to ``Decimal`` via ``str`` so 0.1 stays 0.1."""
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:  # pragma: no cover - defensive
        raise ValueError(f"cannot convert {value!r} to Decimal") from exc


def decimals_of(step: float) -> int:
    """Number of decimal places implied by a step (0.01 -> 2, 1 -> 0)."""
    d = dec(step).normalize()
    exponent = d.as_tuple().exponent
    if not isinstance(exponent, int):  # pragma: no cover - NaN/Infinity guard
        return 8
    return max(0, -exponent)


def quantise(value: float, step: float, mode: RoundMode = "nearest") -> float:
    """Snap ``value`` onto the ``step`` grid.

    ``step <= 0`` is treated as "no grid" and the value is returned unchanged.
    A tiny relative epsilon is applied so a value that is mathematically on the
    grid but a float-hair below it (``0.29999999999999993`` for step ``0.01``)
    does not get floored down an entire step.
    """
    if step is None or step <= 0:
        return float(value)

    d_value = dec(value)
    d_step = dec(step)

    ratio = d_value / d_step
    # Nudge away float representation error before applying a directional round.
    if mode == "floor":
        ratio += Decimal("1e-9")
    elif mode == "ceil":
        ratio -= Decimal("1e-9")

    units = ratio.quantize(Decimal(1), rounding=_MODE_MAP[mode])
    return float(units * d_step)


def round_volume(volume: float, step: float, mode: RoundMode = "floor") -> float:
    """Quantise a volume and clean up trailing float noise."""
    snapped = quantise(volume, step, mode)
    return round(snapped, max(2, decimals_of(step)))


def round_price(price: float, digits: int, tick_size: float = 0.0) -> float:
    """Snap a price to the instrument's tick grid, then to its digit count."""
    value = quantise(price, tick_size, "nearest") if tick_size and tick_size > 0 else price
    return round(value, digits)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def is_multiple_of(value: float, step: float, tolerance: float = 1e-9) -> bool:
    """True when ``value`` sits on the ``step`` grid."""
    if step is None or step <= 0:
        return True
    remainder = abs(dec(value) % dec(step))
    return remainder <= dec(tolerance) or abs(remainder - dec(step)) <= dec(tolerance)


def approx_equal(a: float, b: float, tolerance: float = 1e-9) -> bool:
    return abs(a - b) <= tolerance


def safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Division that yields ``default`` instead of raising on a zero divisor."""
    if not denominator:
        return default
    return numerator / denominator


def pct(part: float, whole: float) -> float:
    """``part`` as a percentage of ``whole`` (0.0 when ``whole`` is falsy)."""
    return safe_div(part, whole, 0.0) * 100.0
