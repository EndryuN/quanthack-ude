"""Pure-numpy indicator helpers. No MT5 dependency so they're unit-testable
and reusable by the backtester."""
from __future__ import annotations

import numpy as np


def ema(x: np.ndarray, span: int) -> np.ndarray:
    if len(x) == 0:
        return x
    alpha = 2.0 / (span + 1.0)
    out = np.empty_like(x, dtype=float)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = alpha * x[i] + (1 - alpha) * out[i - 1]
    return out


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int) -> float:
    """Average true range over the last n bars (absolute price units)."""
    if len(close) < 2:
        return 0.0
    prev_close = close[:-1]
    tr = np.maximum.reduce([
        high[1:] - low[1:],
        np.abs(high[1:] - prev_close),
        np.abs(low[1:] - prev_close),
    ])
    n = min(n, len(tr))
    return float(np.mean(tr[-n:])) if n > 0 else 0.0


def ret_vol(close: np.ndarray, n: int) -> float:
    """Std of simple returns over last n bars (fraction units)."""
    if len(close) < 3:
        return 0.0
    rets = np.diff(close) / close[:-1]
    n = min(n, len(rets))
    return float(np.std(rets[-n:])) if n > 1 else 0.0


def momentum_z(close: np.ndarray, lookback: int) -> float:
    """Return over `lookback` bars normalized by per-bar return volatility.
    A unit-free conviction score: large |z| == strong, persistent move."""
    if len(close) < lookback + 2:
        return 0.0
    total_ret = (close[-1] - close[-1 - lookback]) / close[-1 - lookback]
    rets = np.diff(close[-1 - lookback:]) / close[-1 - lookback:-1]
    sd = np.std(rets)
    if sd == 0:
        return 0.0
    # scale so z is comparable to a per-bar move count
    return float(total_ret / (sd * np.sqrt(lookback)))
