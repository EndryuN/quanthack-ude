"""Backtest the mean-reversion scalp on M1 bars resampled from the local tick
parquet. Costs are charged per round-trip as `cost_frac` (spread/price). The
evidence gate: only arm the scalper live if this clears costs out-of-sample."""
from __future__ import annotations

import glob
import os
import re

from .scalp_signal import scalp_decision


def m1_bars(symbol: str):
    """Resample the symbol's tick parquet to M1 mid closes. Returns
    (closes:list[float], cost_frac:float) where cost_frac = mean spread/price."""
    import pandas as pd
    import pyarrow.parquet as pq
    from .dataloader import DATA_ROOT
    files = sorted(f for f in glob.glob(os.path.join(DATA_ROOT, "*.parquet"))
                   if re.match(rf"{symbol}_\d{{4}}", os.path.basename(f)))
    parts = []
    for f in files:
        df = pq.read_table(f, columns=["time", "bid", "ask"]).to_pandas()
        df["time"] = pd.to_datetime(df["time"]); df = df.set_index("time")
        mid = (df["bid"] + df["ask"]) / 2.0
        spread = (df["ask"] - df["bid"])
        b = pd.DataFrame({"close": mid.resample("1min").last(),
                          "spread": spread.resample("1min").mean()}).dropna()
        parts.append(b)
    if not parts:
        return [], 0.0
    bars = pd.concat(parts).sort_index()
    bars = bars[~bars.index.duplicated(keep="last")]
    closes = [float(x) for x in bars["close"].to_numpy()]
    price = closes[-1] if closes else 1.0
    cost_frac = float(bars["spread"].mean() / price) if price > 0 else 0.0
    return closes, cost_frac


def backtest_scalp(closes, params, cost_frac: float) -> dict:
    """Replay scalp_decision over `closes`. Each closed round-trip pays
    `cost_frac` (in price terms) once. Returns summary stats in bps."""
    position, entry_price, bars_held = 0, 0.0, 0
    trade_returns = []      # net return (fraction) per closed trade
    for i in range(2, len(closes) + 1):
        window = closes[:i]
        price = window[-1]
        if position != 0:
            bars_held += 1
        target, _ = scalp_decision(window, position, entry_price, bars_held, params)
        if position == 0 and target != 0:
            position, entry_price, bars_held = target, price, 0
        elif position != 0 and target != position:
            gross = (price - entry_price) / entry_price * position
            trade_returns.append(gross - cost_frac)   # charge round-trip cost
            position, entry_price, bars_held = 0, 0.0, 0

    n = len(trade_returns)
    if n == 0:
        return {"trades": 0, "win_rate": 0.0, "net_pnl_bps": 0.0,
                "sharpe": 0.0, "max_dd_bps": 0.0}
    wins = sum(1 for r in trade_returns if r > 0)
    net = sum(trade_returns)
    mean = net / n
    var = sum((r - mean) ** 2 for r in trade_returns) / n
    std = var ** 0.5
    sharpe = (mean / std) if std > 0 else 0.0
    cum, peak, mdd = 0.0, 0.0, 0.0
    for r in trade_returns:
        cum += r; peak = max(peak, cum); mdd = min(mdd, cum - peak)
    return {"trades": n, "win_rate": round(wins / n, 4),
            "net_pnl_bps": round(net * 1e4, 2), "sharpe": round(sharpe, 4),
            "max_dd_bps": round(mdd * 1e4, 2)}
