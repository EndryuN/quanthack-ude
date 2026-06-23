"""Variance-aware position sizer with an aggression dial.

Takes per-instrument directional views (direction + conviction in [0,1]) and
turns them into target signed lots. A single `aggression` parameter in [0,1]
interpolates the whole risk posture:

  aggression=0  (SURVIVAL, elimination rounds)
      low gross leverage, diversified across all conviction names, inverse-vol
      weighted so equity stays smooth -> protects against elimination + DD rank.

  aggression=1  (FINALS, go-for-return)
      high gross leverage, concentrated into the highest-conviction names
      (weight = conviction^p with large p) -> maximizes return-rank variance.

The kill-switch and leverage/margin caps in risk.py sit below this and cannot
be overridden. This is the philosophy chosen with the user: no easy alpha, so
size for rank variance while staying survivable.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .client import MT5Client
from .indicators import ret_vol
import MetaTrader5 as mt5


@dataclass
class SizerConfig:
    # Defaults tuned for the organizer's "controlled risk" mandate: even at the
    # top of the aggression dial these stay well inside the risk-discipline
    # tripwires (leverage <28x, single-instrument <90%, margin <90%).
    surv_leverage: float = 2.0     # gross leverage at aggression=0
    max_leverage: float = 6.0      # gross leverage at aggression=1 (well under caps)
    surv_conc_power: float = 1.0   # weight = conviction^power ; low = diversified
    max_conc_power: float = 2.5    # mild concentration only
    surv_single_frac: float = 0.30 # max single-instrument share at aggression=0
    max_single_frac: float = 0.45  # ...at aggression=1 (far below 90% tripwire)
    use_inverse_vol: bool = True   # risk-normalize weights (more at low aggression)
    vol_lookback: int = 48
    vol_bars_tf: int = mt5.TIMEFRAME_M15
    min_conviction: float = 0.15   # ignore views weaker than this


def _lerp(a, b, t):
    return a + (b - a) * t


def build_targets(client: MT5Client, views: dict, equity: float,
                  aggression: float, cfg: SizerConfig | None = None) -> dict:
    """views: {symbol: (direction in {-1,0,1}, conviction in [0,1])}.
    Returns {symbol: target_signed_lots}."""
    cfg = cfg or SizerConfig()
    a = float(np.clip(aggression, 0.0, 1.0))

    active = {s: (d, c) for s, (d, c) in views.items()
              if d != 0 and c >= cfg.min_conviction}
    if not active:
        return {s: 0.0 for s in views}

    power = _lerp(cfg.surv_conc_power, cfg.max_conc_power, a)
    gross_lev = _lerp(cfg.surv_leverage, cfg.max_leverage, a)
    single_cap = _lerp(cfg.surv_single_frac, cfg.max_single_frac, a)

    # raw weight: conviction^power, optionally inverse-vol (more diversifying
    # weight to calmer instruments, which matters most at low aggression)
    raw = {}
    for s, (d, c) in active.items():
        w = c ** power
        if cfg.use_inverse_vol:
            rates = client.bars(s, cfg.vol_bars_tf, cfg.vol_lookback + 6)
            # drop the forming (last) bar so vol — and thus the target — does not
            # jitter every tick within a 15-min window
            if rates is not None and len(rates) > cfg.vol_lookback + 1:
                closed = np.asarray(rates["close"], dtype=float)[:-1]
                vol = ret_vol(closed, cfg.vol_lookback)
            else:
                vol = 0.0
            # inverse-vol risk-normalization, faded out as aggression rises:
            #   a=0 -> full 1/vol weighting (risk parity, smooth equity)
            #   a=1 -> vol exponent 0 -> ignore vol (chase conviction)
            if vol > 0:
                w *= (1.0 / vol) ** (1.0 - a)
        raw[s] = w

    total = sum(raw.values())
    if total <= 0:
        return {s: 0.0 for s in views}
    weights = {s: v / total for s, v in raw.items()}
    weights = _cap_concentration(weights, single_cap)

    gross_notional = gross_lev * equity
    targets = {s: 0.0 for s in views}
    for s, w in weights.items():
        d = active[s][0]
        lots = client.lots_for_notional(s, w * gross_notional)
        targets[s] = lots * d
    return targets


def _cap_concentration(weights: dict, cap: float) -> dict:
    w = dict(weights)
    for _ in range(6):
        over = {s: v for s, v in w.items() if v > cap}
        if not over:
            break
        for s in over:
            w[s] = cap
        capped = sum(over.values())
        free = {s: v for s, v in w.items() if s not in over}
        fmass = sum(free.values())
        if fmass <= 0:
            break
        rem = 1.0 - capped
        for s in free:
            w[s] = rem * (w[s] / fmass)
    return w
