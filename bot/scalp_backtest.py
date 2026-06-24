"""Backtest the mean-reversion scalp on M1 bars resampled from the local tick
parquet. Costs are charged per round-trip as `cost_frac` (spread/price). The
evidence gate: only arm the scalper live if this clears costs out-of-sample."""
from __future__ import annotations

import glob
import os
import re

from .scalp_signal import ScalpParams, scalp_decision

# the entry/tp/stop grid swept for every symbol (bps). Kept here so the CLI and
# the panel's "Run backtest" button sweep the exact same parameter space.
SWEEP_ENTRY = (3.0, 5.0, 8.0)
SWEEP_TP = (3.0, 5.0, 8.0)
SWEEP_STOP = (8.0, 12.0)


def m1_bars(symbol: str):
    """M1 mid closes for `symbol`. Returns (closes:list[float], cost_frac:float)
    where cost_frac = mean spread/price (the per-round-trip cost).

    Tries the local tick parquet first (FX + metals + oil). If the symbol isn't
    in that set (e.g. crypto), falls back to pulling M1 history live from the
    MetaTrader 5 terminal."""
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
        return m1_bars_mt5(symbol)        # no local data -> try the live terminal
    bars = pd.concat(parts).sort_index()
    bars = bars[~bars.index.duplicated(keep="last")]
    closes = [float(x) for x in bars["close"].to_numpy()]
    price = closes[-1] if closes else 1.0
    cost_frac = float(bars["spread"].mean() / price) if price > 0 else 0.0
    return closes, cost_frac


def m1_bars_mt5(symbol: str, count: int = 50000):
    """Pull up to `count` M1 bars for `symbol` straight from a running MT5
    terminal. Returns (closes, cost_frac); ([], 0.0) with a printed note if the
    terminal/history is unavailable. cost_frac is the mean bar spread (in points
    -> price) over the mean close — the realistic per-round-trip cost the live
    scalper would pay on this instrument."""
    try:
        import MetaTrader5 as mt5
    except Exception:
        print("  MetaTrader5 package not installed; cannot pull crypto M1.")
        return [], 0.0
    if not mt5.initialize():
        print(f"  mt5.initialize() failed: {mt5.last_error()} "
              f"(open the MT5 terminal, then retry).")
        return [], 0.0
    try:
        if not mt5.symbol_select(symbol, True):
            print(f"  symbol_select({symbol}) failed: {mt5.last_error()}")
            return [], 0.0
        rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, count)
        if rates is None or len(rates) == 0:
            print(f"  no M1 history for {symbol} from MT5: {mt5.last_error()}")
            return [], 0.0
        info = mt5.symbol_info(symbol)
        point = float(getattr(info, "point", 0.0) or 0.0)
        closes = [float(x) for x in rates["close"]]
        mean_price = sum(closes) / len(closes) if closes else 1.0
        # per-bar spread is reported in points; convert to price and average.
        spreads = [float(s) * point for s in rates["spread"]]
        cost_frac = (sum(spreads) / len(spreads) / mean_price) \
            if mean_price > 0 and spreads else 0.0
        print(f"  pulled {len(closes)} M1 bars for {symbol} from MT5.")
        return closes, cost_frac
    finally:
        mt5.shutdown()


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


def sweep_over_closes(closes, cost_frac: float) -> dict:
    """Run the entry/tp/stop grid over one M1 close series. Returns
    {"rows": [...], "best": row|None} where each row is the param triple merged
    with backtest_scalp's stats. `best` is the highest-Sharpe row with >=30
    trades (the arming-gate candidate). Pure function -> unit-testable."""
    rows, best = [], None
    for entry in SWEEP_ENTRY:
        for tp in SWEEP_TP:
            for stop in SWEEP_STOP:
                p = ScalpParams(ema_period=20, entry_bps=entry, exit_bps=1.0,
                                tp_bps=tp, stop_bps=stop, time_stop_bars=10)
                r = backtest_scalp(closes, p, cost_frac)
                row = {"entry": entry, "tp": tp, "stop": stop, **r}
                rows.append(row)
                if r["trades"] >= 30 and (best is None or r["sharpe"] > best["sharpe"]):
                    best = row
    return {"rows": rows, "best": best}


def sweep_scalp(symbol: str) -> dict:
    """Load M1 data for `symbol` (local parquet, else live MT5) and sweep the
    grid. Returns a JSON-friendly dict the CLI and the panel both render.
    {"available": False} when no M1 data could be sourced."""
    closes, cost_frac = m1_bars(symbol)
    if not closes:
        return {"available": False, "symbol": symbol}
    out = sweep_over_closes(closes, cost_frac)
    out.update({"available": True, "symbol": symbol, "bars": len(closes),
                "cost_bps": round(cost_frac * 1e4, 2)})
    return out
