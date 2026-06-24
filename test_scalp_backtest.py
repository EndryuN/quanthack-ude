"""Backtest accounting is deterministic; zero cost vs real cost behaves sanely."""
from bot.scalp_signal import ScalpParams
from bot.scalp_backtest import backtest_scalp

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
print("OK")
