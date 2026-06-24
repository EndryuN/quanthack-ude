"""Backtest accounting is deterministic; zero cost vs real cost behaves sanely."""
from bot.scalp_signal import ScalpParams
from bot.scalp_backtest import backtest_scalp, sweep_over_closes, SWEEP_ENTRY, SWEEP_TP, SWEEP_STOP

p = ScalpParams(ema_period=3, entry_bps=10.0, exit_bps=2.0, tp_bps=8.0,
                stop_bps=15.0, time_stop_bars=5)
# a mean-reverting saw-tooth: fade entries should fire and close
closes = ([100.0, 100.0, 100.0, 100.3, 100.0, 99.7, 100.0] * 20)

r0 = backtest_scalp(closes, p, cost_frac=0.0)
assert r0["trades"] > 0, r0
assert set(r0) == {"trades", "win_rate", "net_pnl_bps", "sharpe", "max_dd_bps"}

# determinism
assert backtest_scalp(closes, p, cost_frac=0.0) == r0

# adding cost can only reduce net P&L
r1 = backtest_scalp(closes, p, cost_frac=0.0010)   # 10 bps round-trip
assert r1["net_pnl_bps"] <= r0["net_pnl_bps"]

# sweep covers the full grid and picks the best >=30-trade row by Sharpe
sw = sweep_over_closes(closes, cost_frac=0.0)
assert len(sw["rows"]) == len(SWEEP_ENTRY) * len(SWEEP_TP) * len(SWEEP_STOP), sw
for row in sw["rows"]:                       # each row carries its params + stats
    assert {"entry", "tp", "stop", "trades", "sharpe", "net_pnl_bps"} <= set(row)
if sw["best"] is not None:
    assert sw["best"]["trades"] >= 30
    best_sharpe = max(r["sharpe"] for r in sw["rows"] if r["trades"] >= 30)
    assert sw["best"]["sharpe"] == best_sharpe
print("OK")
