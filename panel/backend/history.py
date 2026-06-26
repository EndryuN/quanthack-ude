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
# FX -> Yahoo spot pairs; metals -> Yahoo futures (≈ spot; the basis is removed by
# anchoring the series to the broker's latest price in the caller).
_YAHOO_SYM = {"EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X", "USDJPY": "USDJPY=X", "AUDUSD": "AUDUSD=X",
              "USDCAD": "USDCAD=X", "USDCHF": "USDCHF=X", "EURGBP": "EURGBP=X", "EURCHF": "EURCHF=X",
              "XAUUSD": "GC=F", "XAGUSD": "SI=F"}


def _resample_tuples(rows: list, rule: str) -> list:
    """Resample [(epoch,o,h,l,c)] to a coarser bar (e.g. 60m -> '4h')."""
    if not rows:
        return rows
    try:
        import pandas as pd
        idx = pd.to_datetime([r[0] for r in rows], unit="s")
        df = pd.DataFrame({"open": [r[1] for r in rows], "high": [r[2] for r in rows],
                           "low": [r[3] for r in rows], "close": [r[4] for r in rows]}, index=idx)
        out = pd.DataFrame({"open": df["open"].resample(rule).first(),
                            "high": df["high"].resample(rule).max(),
                            "low": df["low"].resample(rule).min(),
                            "close": df["close"].resample(rule).last()}).dropna()
        return [(int(t.timestamp()), float(r.open), float(r.high), float(r.low), float(r.close))
                for t, r in out.iterrows()]
    except Exception:
        return rows


def yahoo_history(symbol: str, tf: str) -> list:
    """Deep FX/metals history via Yahoo's chart API (no key). 1d -> 5y daily; 4h ->
    60m resampled (~2.8y). [] for unsupported symbol/tf or on failure."""
    ysym = _YAHOO_SYM.get(symbol)
    if not ysym:
        return []
    if tf == "1d":
        interval, rng = "1d", "5y"
    elif tf in ("1h", "4h"):
        interval, rng = "60m", "730d"   # ~2.8y of hourly; 4h resampled from it
    else:
        return []   # 1m/15m stay on the broker feed (recent-accurate)
    try:
        import httpx
        r = httpx.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ysym}",
                      params={"interval": interval, "range": rng}, timeout=10.0,
                      headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        res = (r.json().get("chart") or {}).get("result")
        if not res:
            return []
        res = res[0]
        ts = res.get("timestamp") or []
        q = res["indicators"]["quote"][0]
        rows = []
        for i, t in enumerate(ts):
            o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
            if None in (o, h, l, c):
                continue
            rows.append((int(t), float(o), float(h), float(l), float(c)))
        return _resample_tuples(rows, "4h") if tf == "4h" else rows
    except Exception:
        return []


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
