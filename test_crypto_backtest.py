"""Phase-2 crypto backtest replay + walk-forward gate (pure, no MT5)."""
import math
from bot.crypto_signal import CryptoParams
from bot.crypto_backtest import backtest_crypto, walk_forward


def trend(n, drift=0.0008, wiggle=0.0010, period=3.0):
    """Deterministic up-trend with oscillation -> varied trade P&L (real Sharpe)."""
    out, p = [], 100.0
    for i in range(n):
        p *= (1 + drift + wiggle * math.sin(i / period))
        out.append(p)
    return out

P = CryptoParams(lookback=3, entry_bps=25.0)
COST = 0.0005   # 5 bps round-trip

# steadily rising series -> long breakouts, broker TP fires repeatedly -> net > 0
rising = [100.0 * (1.001 ** i) for i in range(120)]     # +~10 bps/bar
r = backtest_crypto(rising, P, COST, tp_bps=20.0, stop_bps=40.0)
assert r["trades"] > 0 and r["net_pnl_bps"] > 0, r
assert r["win_rate"] > 0.5, r

# falling series -> short breakouts also profit
falling = [100.0 * (0.999 ** i) for i in range(120)]
rf = backtest_crypto(falling, P, COST, tp_bps=20.0, stop_bps=40.0)
assert rf["trades"] > 0 and rf["net_pnl_bps"] > 0, rf

# flat series -> no breakout -> no trades
flat = [100.0] * 120
assert backtest_crypto(flat, P, COST, tp_bps=20.0, stop_bps=40.0)["trades"] == 0

# cost matters: a punishing cost turns the same trades net-negative
r_hi = backtest_crypto(rising, P, 0.0030, tp_bps=20.0, stop_bps=40.0)
assert r_hi["net_pnl_bps"] < r["net_pnl_bps"]

# walk-forward gate: trains on first 60%, evaluates OUT-OF-SAMPLE on the rest
wf = walk_forward(trend(400), COST, engine_sharpe=0.1)
assert wf["available"] and isinstance(wf["arm"], bool)
assert wf["train"] is not None and wf["test"] is not None and wf["params"] is not None
assert wf["train"]["trades"] >= 30                      # train cell cleared MIN_TRADES
assert wf["test"]["net_pnl_bps"] > 0                    # OOS profitable on a real trend

# a flat market never clears the gate (no train cell hits MIN_TRADES -> can't arm)
wf_flat = walk_forward([100.0] * 300, COST, engine_sharpe=0.1)
assert wf_flat["arm"] is False and wf_flat["test"] is None, wf_flat

# the arm decision honors engine_sharpe: an impossibly high bar blocks arming
wf_hard = walk_forward(trend(400), COST, engine_sharpe=999.0)
assert wf_hard["arm"] is False, wf_hard

print("OK")
