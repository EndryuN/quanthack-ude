"""Bar-replay backtester. Reuses the LIVE HybridStrategy unchanged by feeding
it a BacktestClient that mimics the methods the strategy calls (bars, notional,
lots_for_notional). This guarantees backtest == live logic.

Marking convention: per-bar USD PnL = signed_lots * per_lot_usd_prev * ret,
where ret is close-to-close. First-order exact for *USD symbols and a good
approximation for USD-base/JPY pairs (second-order error), which is fine for
parameter tuning. Costs modelled as a per-symbol spread fraction on traded
notional.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .strategy import HybridStrategy, StrategyConfig
from .config import INITIAL_EQUITY, ACCOUNT_LEVERAGE, RiskLimits


@dataclass
class SymbolMeta:
    contract_size: float
    volume_min: float
    volume_max: float
    volume_step: float
    usd_base: bool          # True for USDxxx (quote is foreign)
    cost_frac: float        # round-trip-ish spread as fraction of notional


class BacktestClient:
    """Minimal stand-in for MT5Client exposing only what the strategy uses."""
    def __init__(self, data: dict, meta: dict):
        # data[symbol] = structured array with fields time/open/high/low/close
        self.data = data
        self.meta = meta
        self.t = 0                      # current bar index (global grid)
        self.grid = None                # shared timestamp grid
        self._idx = {}                  # symbol -> per-symbol index aligned to grid

    def price(self, symbol):
        i = self._idx[symbol][self.t]
        if i < 0:
            return None
        return float(self.data[symbol]["close"][i])

    def bars(self, symbol, timeframe, count):
        i = self._idx[symbol][self.t]
        if i < 0 or i < 5:
            return None
        lo = max(0, i - count + 1)
        return self.data[symbol][lo:i + 1]

    def _per_lot_usd(self, symbol):
        m = self.meta[symbol]
        p = self.price(symbol)
        if p is None:
            return 0.0
        return m.contract_size if m.usd_base else m.contract_size * p

    def notional(self, symbol, lots):
        return self._per_lot_usd(symbol) * lots

    def lots_for_notional(self, symbol, usd_notional):
        m = self.meta[symbol]
        per_lot = self._per_lot_usd(symbol)
        if per_lot <= 0 or usd_notional <= 0:
            return 0.0
        lots = usd_notional / per_lot
        step = m.volume_step or 0.01
        lots = round(lots / step) * step
        lots = max(m.volume_min, min(m.volume_max, lots))
        return round(lots, 2)


@dataclass
class BacktestResult:
    equity_curve: list = field(default_factory=list)
    times: list = field(default_factory=list)
    n_trades: int = 0
    ret: float = 0.0
    max_dd: float = 0.0
    sharpe: float = 0.0
    final_equity: float = 0.0
    blew_up: bool = False


def _sharpe(equity):
    eq = np.asarray(equity, dtype=float)
    if len(eq) < 2:
        return 0.0
    rets = np.diff(eq) / eq[:-1]
    rets = rets[np.isfinite(rets)]
    if len(rets) < 1 or np.std(rets) == 0:
        return 0.0
    return float(np.mean(rets) / np.std(rets))


def _max_dd(equity):
    eq = np.asarray(equity, dtype=float)
    peak = np.maximum.accumulate(eq)
    return float(np.max((peak - eq) / peak))


def run_backtest(data: dict, meta: dict, scfg: StrategyConfig | None = None,
                 risk: RiskLimits | None = None, symbols=None, warmup=90,
                 verbose=False) -> BacktestResult:
    symbols = symbols or list(data.keys())
    scfg = scfg or StrategyConfig()
    risk = risk or RiskLimits()
    strat = HybridStrategy(cfg=scfg)

    # build a shared timestamp grid (union of all symbol bar times)
    all_times = sorted(set().union(*[set(data[s]["time"].tolist()) for s in symbols]))
    grid = np.asarray(all_times)
    client = BacktestClient(data, meta)
    client.grid = grid
    # for each symbol, map grid index -> bar index (-1 if no bar yet)
    for s in symbols:
        ts = data[s]["time"]
        pos = np.searchsorted(ts, grid, side="right") - 1
        # mask grid points before the symbol's first bar
        pos = np.where(grid < ts[0], -1, pos)
        client._idx[s] = pos

    equity = INITIAL_EQUITY
    peak = equity
    lots = {s: 0.0 for s in symbols}     # signed lots held
    prev_price = {s: None for s in symbols}
    curve, times = [], []
    n_trades = 0
    blew_up = False

    for t in range(len(grid)):
        client.t = t

        # 1. mark-to-market: PnL from prev close to this close
        for s in symbols:
            p = client.price(s)
            pp = prev_price[s]
            if p is not None and pp is not None and pp != 0 and lots[s] != 0:
                m = meta[s]
                per_lot_prev = m.contract_size if m.usd_base else m.contract_size * pp
                equity += lots[s] * per_lot_prev * ((p - pp) / pp)
            if p is not None:
                prev_price[s] = p

        peak = max(peak, equity)
        dd = (peak - equity) / peak if peak > 0 else 0.0
        round_dd = (INITIAL_EQUITY - equity) / INITIAL_EQUITY

        if t < warmup:
            curve.append(equity); times.append(int(grid[t])); continue

        # 2. risk: gross leverage, margin proxy, kill-switch
        gross = sum(abs(lots[s]) * client._per_lot_usd(s)
                    for s in symbols if client.price(s) is not None)
        used_margin = gross / ACCOUNT_LEVERAGE
        margin_level = (equity / used_margin * 100) if used_margin > 0 else 1e9

        kill = (equity <= 0 or round_dd >= risk.daily_max_drawdown or
                (used_margin > 0 and margin_level < risk.kill_switch_margin_level))
        if equity <= 0:
            blew_up = True
        if kill:
            for s in symbols:
                if lots[s] != 0:
                    n_trades += 1
                    p = client.price(s)
                    if p is not None:
                        equity -= abs(lots[s]) * client._per_lot_usd(s) * meta[s].cost_frac
                    lots[s] = 0.0
            curve.append(equity); times.append(int(grid[t]))
            if blew_up:
                break
            continue

        # 3. strategy targets (block new risk when over caps); honor cadence
        if (t - warmup) % max(1, scfg.decision_every) != 0:
            curve.append(equity); times.append(int(grid[t])); continue
        targets = strat.target_positions(client, symbols, equity, held=lots)
        allow_new = (gross / equity <= risk.max_gross_leverage and
                     used_margin / equity <= risk.max_margin_usage)

        # 4. reconcile
        for s in symbols:
            if client.price(s) is None:
                continue
            tgt = targets.get(s, 0.0)
            cur = lots[s]
            if not allow_new and abs(tgt) > abs(cur) and np.sign(tgt) == np.sign(cur):
                continue                         # don't increase when blocked
            if not strat.should_rebalance(cur, tgt, 0.0):
                continue
            traded = abs(tgt - cur)
            if traded <= 0:
                continue
            equity -= traded * client._per_lot_usd(s) * meta[s].cost_frac
            lots[s] = tgt
            n_trades += 1

        curve.append(equity); times.append(int(grid[t]))

    res = BacktestResult(
        equity_curve=curve, times=times, n_trades=n_trades,
        ret=(equity - INITIAL_EQUITY) / INITIAL_EQUITY,
        max_dd=_max_dd(curve) if curve else 0.0,
        sharpe=_sharpe(curve), final_equity=equity, blew_up=blew_up,
    )
    if verbose:
        print(f"  ret={res.ret*100:+.2f}% dd={res.max_dd*100:.2f}% "
              f"sharpe={res.sharpe:.3f} trades={res.n_trades} "
              f"final={res.final_equity:,.0f} blewup={res.blew_up}")
    return res
