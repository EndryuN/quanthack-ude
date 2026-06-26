"""Deeper instrument-chart history from sources beyond the MT5 terminal's cache.

- FX + metals: the backtest pricer bars in cache/<sym>_M15.parquet — the SAME price
  source as the live broker feed, so it stitches onto live MT5 seamlessly.
- crypto: Binance public klines (free, no key, deep history) for the whole series.

Best-effort: any failure returns [] so the caller falls back to live MT5. Each bar
is a tuple (epoch_seconds, open, high, low, close)."""
import os

_CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "cache")
_RULE = {"1h": "1h", "4h": "4h", "1d": "1D"}          # pandas resample rule (15m is native)
_BINANCE_SYM = {"BTCUSD": "BTCUSDT", "ETHUSD": "ETHUSDT", "SOLUSD": "SOLUSDT", "XRPUSD": "XRPUSDT"}
_BINANCE_IV = {"1m": "1m", "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}


def parquet_history(symbol: str, tf: str) -> list:
    """Backtest-cache bars for FX/metals, resampled to tf. [] if absent, or for
    tf=1m (the cache is 15m and can't synthesize 1m)."""
    if tf == "1m":
        return []
    path = os.path.join(_CACHE, f"{symbol}_M15.parquet")
    if not os.path.exists(path):
        return []
    try:
        import pandas as pd
        df = pd.read_parquet(path)[["open", "high", "low", "close"]].sort_index()
        rule = _RULE.get(tf)
        if rule:
            df = pd.DataFrame({
                "open": df["open"].resample(rule).first(),
                "high": df["high"].resample(rule).max(),
                "low": df["low"].resample(rule).min(),
                "close": df["close"].resample(rule).last(),
            }).dropna()
        return [(int(t.timestamp()), float(r.open), float(r.high), float(r.low), float(r.close))
                for t, r in df.iterrows()]
    except Exception:
        return []


def binance_history(symbol: str, tf: str, limit: int = 1000) -> list:
    """Binance public klines for crypto. [] for non-crypto symbols or on any failure."""
    sym = _BINANCE_SYM.get(symbol)
    iv = _BINANCE_IV.get(tf)
    if not sym or not iv:
        return []
    try:
        import httpx
        r = httpx.get("https://api.binance.com/api/v3/klines",
                      params={"symbol": sym, "interval": iv, "limit": max(1, min(limit, 1000))},
                      timeout=8.0)
        r.raise_for_status()
        return [(int(k[0] // 1000), float(k[1]), float(k[2]), float(k[3]), float(k[4]))
                for k in r.json()]
    except Exception:
        return []
