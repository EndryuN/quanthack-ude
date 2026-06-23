"""Hybrid strategy: vol-targeted momentum CORE + conviction AGGRESSION SLEEVE.

Per instrument it forms a trend direction (EMA regime AND momentum must agree,
else flat) and a unit-free conviction score. Positions are sized inverse-vol so
each name contributes comparable risk (smooth equity -> good DD/Sharpe). The
sleeve scales gross exposure up toward the cap when conviction is high, tilted
toward crypto where the moves are largest.

Output: target SIGNED lots per symbol. The engine reconciles to these.
All sizing respects the concentration / leverage caps; the risk engine and
kill-switch are an independent backstop below this.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import MetaTrader5 as mt5

from .client import MT5Client
from .config import CRYPTO
from .indicators import ema, ret_vol, momentum_z


@dataclass
class StrategyConfig:
    mode: str = "momentum"        # "momentum" or "meanrev" (flip direction)
    timeframe: int = mt5.TIMEFRAME_M15
    bars: int = 200
    ema_fast: int = 20            # ~5h on M15
    ema_slow: int = 80            # ~20h on M15
    mom_lookback: int = 32        # ~8h
    vol_lookback: int = 48        # ~12h

    entry_z: float = 0.12         # min conviction to OPEN a position
    exit_z: float = 0.05          # hold until conviction decays below this

    core_leverage: float = 6.0    # gross target when conviction is average
    max_leverage: float = 10.0    # gross target when conviction is maxed (sleeve)
    max_single_frac: float = 0.45 # cap any one instrument's share of gross

    sleeve_threshold: float = 0.30  # conviction above this gets size-boosted
    sleeve_gain: float = 2.0
    sleeve_cap: float = 3.0         # max per-instrument conviction multiplier
    crypto_boost: float = 1.30      # extra tilt to crypto names in the sleeve

    rebalance_frac: float = 0.50    # only adjust a position if target drifts >50%
    decision_every: int = 4         # recompute targets every N bars (cadence)


@dataclass
class Signal:
    symbol: str
    direction: int       # +1 long, -1 short, 0 flat
    conviction: float    # |momentum z|
    vol: float           # per-bar return vol (sizing input)


@dataclass
class HybridStrategy:
    cfg: StrategyConfig = field(default_factory=StrategyConfig)

    def signals(self, client: MT5Client, symbols) -> dict:
        out = {}
        for s in symbols:
            rates = client.bars(s, self.cfg.timeframe, self.cfg.bars)
            if rates is None or len(rates) < self.cfg.ema_slow + 5:
                out[s] = Signal(s, 0, 0.0, 0.0)
                continue
            close = np.asarray(rates["close"], dtype=float)
            ef = ema(close, self.cfg.ema_fast)[-1]
            es = ema(close, self.cfg.ema_slow)[-1]
            regime = 1 if ef > es else -1
            z = momentum_z(close, self.cfg.mom_lookback)
            mom_dir = 1 if z > 0 else (-1 if z < 0 else 0)
            vol = ret_vol(close, self.cfg.vol_lookback)
            # require regime and momentum to agree, else flat
            direction = regime if (regime == mom_dir and mom_dir != 0) else 0
            if self.cfg.mode == "meanrev":
                direction = -direction      # fade the move instead of riding it
            out[s] = Signal(s, direction, abs(z), vol)
        return out

    def target_positions(self, client: MT5Client, symbols, equity: float,
                         held: dict | None = None) -> dict:
        """Return {symbol: target_signed_lots}. `held` maps symbol->signed lots
        currently open; used for exit hysteresis so we keep a position while its
        conviction is between exit_z and entry_z instead of churning."""
        import numpy as _np
        held = held or {}
        sigs = self.signals(client, symbols)

        def is_active(s, sg):
            if sg.direction == 0 or sg.vol <= 0:
                return False
            if sg.conviction >= self.cfg.entry_z:
                return True
            # hysteresis: keep an existing same-direction position until exit_z
            cur = held.get(s, 0.0)
            return (cur != 0 and _np.sign(cur) == sg.direction
                    and sg.conviction >= self.cfg.exit_z)

        active = {s: sg for s, sg in sigs.items() if is_active(s, sg)}
        if not active:
            return {s: 0.0 for s in symbols}

        # --- inverse-vol risk-parity weights, conviction & crypto tilted ---
        raw = {}
        for s, sg in active.items():
            boost = 1.0 + self.cfg.sleeve_gain * max(0.0, sg.conviction - self.cfg.sleeve_threshold)
            boost = min(boost, self.cfg.sleeve_cap)
            if s in CRYPTO:
                boost *= self.cfg.crypto_boost
            raw[s] = (1.0 / sg.vol) * boost   # inverse-vol * conviction boost

        total = sum(raw.values())
        weights = {s: v / total for s, v in raw.items()}   # sum |w| = 1

        # cap single-instrument concentration, then renormalize the rest
        weights = self._cap_concentration(weights)

        # --- gross leverage target scales with average conviction (sleeve) ---
        avg_conv = np.mean([sg.conviction for sg in active.values()])
        scale = np.clip((avg_conv - self.cfg.entry_z) /
                        max(self.cfg.sleeve_threshold - self.cfg.entry_z, 1e-9), 0.0, 1.0)
        gross_lev = self.cfg.core_leverage + (self.cfg.max_leverage - self.cfg.core_leverage) * scale
        gross_notional = gross_lev * equity

        # --- convert weights -> signed lots ---
        targets = {s: 0.0 for s in symbols}
        for s, w in weights.items():
            notional = w * gross_notional
            lots = client.lots_for_notional(s, notional)
            targets[s] = lots * active[s].direction
        return targets

    def _cap_concentration(self, weights: dict) -> dict:
        cap = self.cfg.max_single_frac
        w = dict(weights)
        for _ in range(5):
            over = {s: v for s, v in w.items() if abs(v) > cap}
            if not over:
                break
            for s in over:
                w[s] = cap if w[s] > 0 else -cap
            # renormalize the uncapped names to absorb the remainder
            capped_mass = sum(abs(v) for v in over.values())
            free = {s: v for s, v in w.items() if s not in over}
            free_mass = sum(abs(v) for v in free.values())
            if free_mass <= 0:
                break
            remainder = 1.0 - capped_mass
            for s in free:
                w[s] = remainder * (w[s] / free_mass)
        return w

    def should_rebalance(self, current_lots: float, target_lots: float,
                         vol_min: float) -> bool:
        """Avoid churn: only trade on direction flip, open, full close, or a
        target drift beyond rebalance_frac of the current size."""
        cur_dir = np.sign(current_lots)
        tgt_dir = np.sign(target_lots)
        if cur_dir != tgt_dir:
            return True                      # flip or open or close
        if current_lots == 0:
            return target_lots != 0
        return abs(target_lots - current_lots) / abs(current_lots) > self.cfg.rebalance_frac
