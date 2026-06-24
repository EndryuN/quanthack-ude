"""Pure mean-reversion ('fade') scalp logic. No MT5, no I/O — fully testable.

The idea: when price stretches away from a short EMA by more than `entry_bps`,
fade it (bet on the snap-back). Exit on revert-to-mean, a small take-profit, a
tight adverse stop, or a time stop. `position` and the returned target are the
desired NET direction for the symbol: -1 short, 0 flat, +1 long.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ScalpParams:
    ema_period: int = 20
    entry_bps: float = 5.0      # deviation from EMA to enter a fade
    exit_bps: float = 1.0       # deviation band around the mean to exit into
    tp_bps: float = 4.0         # favorable move that takes profit
    stop_bps: float = 8.0       # adverse move that stops out
    time_stop_bars: int = 10    # max bars to hold


def _ema(closes, period: int) -> float:
    if not closes:
        return 0.0
    k = 2.0 / (period + 1)
    e = closes[0]
    for c in closes[1:]:
        e = c * k + e * (1 - k)
    return e


def scalp_decision(closes, position: int, entry_price: float,
                   bars_held: int, p: ScalpParams):
    """Return (target_direction, reason). target/position in {-1, 0, +1}."""
    if len(closes) < 2:
        return position, "insufficient data"
    price = closes[-1]
    ema = _ema(closes[-p.ema_period:], p.ema_period)
    if ema <= 0:
        return position, "no ema"
    dev_bps = (price - ema) / ema * 1e4

    if position == 0:
        if dev_bps > p.entry_bps:
            return -1, f"fade short dev={dev_bps:.1f}bps"
        if dev_bps < -p.entry_bps:
            return 1, f"fade long dev={dev_bps:.1f}bps"
        return 0, "flat"

    # in a position -> check exits. move_bps>0 means the trade is in profit.
    move_bps = (price - entry_price) / entry_price * 1e4 * position
    if move_bps >= p.tp_bps:
        return 0, f"take-profit {move_bps:.1f}bps"
    if move_bps <= -p.stop_bps:
        return 0, f"stop {move_bps:.1f}bps"
    if bars_held >= p.time_stop_bars:
        return 0, f"time stop {bars_held}b"
    if position == -1 and dev_bps <= p.exit_bps:
        return 0, f"reverted dev={dev_bps:.1f}bps"
    if position == 1 and dev_bps >= -p.exit_bps:
        return 0, f"reverted dev={dev_bps:.1f}bps"
    return position, "hold"
