"""Pure forward-projection geometry for the per-symbol chart. The models output
direction + confidence (NOT price targets), so this draws an honest directional
cone: the mid line slopes with direction*confidence; the cone half-width grows
with the instrument's per-bar volatility and sqrt(time)."""
from __future__ import annotations

import math

_DIR = {"long": 1.0, "short": -1.0, "flat": 0.0}

# how far a confidence=1 view projects the mid over `horizon_bars`, as a
# fraction of price-per-unit-vol. Tuned so a strong call leans visibly but
# stays within a few vol units (no fabricated magnitude claims).
_SLOPE_VOL_UNITS = 6.0


def projection_cone(price: float, direction: str, confidence: float,
                    vol: float, horizon_bars: int = 8) -> dict:
    """Returns {mid, upper, lower}, each a list of [step, price]."""
    d = _DIR.get(direction, 0.0)
    vol = max(vol, 1e-6)
    # total mid drift over the horizon, in price terms
    drift = d * confidence * _SLOPE_VOL_UNITS * vol * price
    mid, upper, lower = [], [], []
    for i in range(horizon_bars + 1):
        frac = i / horizon_bars
        m = price + drift * frac
        half = price * vol * math.sqrt(i)   # band widens with sqrt(time)
        mid.append([i, round(m, 8)])
        upper.append([i, round(m + half, 8)])
        lower.append([i, round(m - half, 8)])
    return {"mid": mid, "upper": upper, "lower": lower}
