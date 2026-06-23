"""Parse the pricer-output L2 parquet tick files into M15 OHLC bars and cache
them. Reads only time/bid/ask columns for speed. The 20GB raw set is parsed
once; subsequent runs load the small cached bars."""
from __future__ import annotations

import os
import glob
import re

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

DATA_ROOT = r"C:\Users\ipode\Desktop\quanthack\pricer-output-2026-05-11_2026-06-10"
CACHE_DIR = r"C:\Users\ipode\Desktop\quanthack\cache"

# competition instruments that actually exist in the dataset (no crypto)
BACKTEST_SYMBOLS = ["AUDUSD", "EURCHF", "EURGBP", "EURUSD", "GBPUSD",
                    "USDCAD", "USDCHF", "USDJPY", "XAGUSD", "XAUUSD"]

BAR = "15min"


def _bars_from_file(path: str) -> pd.DataFrame:
    tbl = pq.read_table(path, columns=["time", "bid", "ask"])
    df = tbl.to_pandas()
    df["time"] = pd.to_datetime(df["time"])
    df = df.set_index("time")
    mid = (df["bid"] + df["ask"]) / 2.0
    spread = (df["ask"] - df["bid"])
    o = mid.resample(BAR).first()
    h = mid.resample(BAR).max()
    l = mid.resample(BAR).min()
    c = mid.resample(BAR).last()
    sp = spread.resample(BAR).mean()
    out = pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "spread": sp})
    return out.dropna()


def build_cache(symbols=BACKTEST_SYMBOLS, verbose=True):
    os.makedirs(CACHE_DIR, exist_ok=True)
    all_files = glob.glob(os.path.join(DATA_ROOT, "*.parquet"))
    for sym in symbols:
        files = sorted(f for f in all_files
                       if re.match(rf"{sym}_\d{{4}}", os.path.basename(f)))
        if not files:
            if verbose:
                print(f"  {sym}: no files")
            continue
        parts = []
        for i, f in enumerate(files):
            parts.append(_bars_from_file(f))
            if verbose:
                print(f"  {sym}: {i+1}/{len(files)} {os.path.basename(f)}", end="\r")
        bars = pd.concat(parts).sort_index()
        bars = bars[~bars.index.duplicated(keep="last")]
        out = os.path.join(CACHE_DIR, f"{sym}_M15.parquet")
        bars.to_parquet(out)
        if verbose:
            print(f"  {sym}: {len(bars)} bars {bars.index[0]} -> {bars.index[-1]}      ")


def load_bars(symbols=BACKTEST_SYMBOLS):
    """Return data,meta dicts compatible with bot.backtest.run_backtest."""
    from .backtest import SymbolMeta
    data, meta = {}, {}
    for sym in symbols:
        path = os.path.join(CACHE_DIR, f"{sym}_M15.parquet")
        if not os.path.exists(path):
            continue
        df = pd.read_parquet(path)
        # resolution-robust: numpy datetime64 -> seconds (pandas 3.0 may use us)
        epoch = df.index.values.astype("datetime64[s]").astype("int64")
        rec = np.empty(len(df), dtype=[("time", "i8"), ("open", "f8"),
                                       ("high", "f8"), ("low", "f8"),
                                       ("close", "f8")])
        rec["time"] = epoch
        rec["open"] = df["open"].to_numpy()
        rec["high"] = df["high"].to_numpy()
        rec["low"] = df["low"].to_numpy()
        rec["close"] = df["close"].to_numpy()
        data[sym] = rec
        price = float(df["close"].iloc[-1])
        spread = float(df["spread"].mean())
        meta[sym] = SymbolMeta(
            contract_size=_CONTRACT.get(sym, 100000.0),
            volume_min=0.01, volume_max=100.0, volume_step=0.01,
            usd_base=(sym.startswith("USD") and not sym.endswith("USD")),
            cost_frac=max(spread / price, 1e-5) if price > 0 else 1e-4,
        )
    return data, meta


# contract sizes confirmed from the live terminal (probe.py)
_CONTRACT = {
    "AUDUSD": 100000.0, "EURCHF": 100000.0, "EURGBP": 100000.0,
    "EURUSD": 100000.0, "GBPUSD": 100000.0, "USDCAD": 100000.0,
    "USDCHF": 100000.0, "USDJPY": 100000.0, "XAGUSD": 5000.0, "XAUUSD": 100.0,
}
