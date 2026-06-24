"""Pure mean-reversion fade decisions. No MT5.

Note on the numbers: with ema_period=3 the EMA reacts fast (k=0.5), so a 1-bar
spike only pulls the mean ~halfway. The inputs below are sized so each
deviation clearly clears the relevant threshold (hand-computed)."""
from bot.scalp_signal import ScalpParams, scalp_decision, _ema

p = ScalpParams(ema_period=3, entry_bps=10.0, exit_bps=2.0, tp_bps=8.0,
                stop_bps=15.0, time_stop_bars=5)

# _ema of a flat series is that value
assert abs(_ema([100.0, 100.0, 100.0], 3) - 100.0) < 1e-9

# FLAT + price stretched ABOVE the mean -> fade SHORT (-1).
# ema([100,100,100.5])=100.25, price 100.5 -> +24.9bps > 10
tgt, why = scalp_decision([100.0, 100.0, 100.0, 100.5], 0, 0.0, 0, p)
assert tgt == -1, (tgt, why)

# FLAT + price stretched BELOW the mean -> fade LONG (+1). ema=99.75 -> -25bps
tgt, _ = scalp_decision([100.0, 100.0, 100.0, 99.5], 0, 0.0, 0, p)
assert tgt == +1

# FLAT + price near the mean -> stay flat (~0.5bps)
tgt, _ = scalp_decision([100.0, 100.0, 100.0, 100.01], 0, 0.0, 0, p)
assert tgt == 0

# SHORT in profit past tp_bps -> exit. entry 100.2, now 100.1 => +9.98bps >= 8
tgt, why = scalp_decision([100.0, 100.0, 100.0, 100.1], -1, 100.2, 1, p)
assert tgt == 0 and "profit" in why.lower(), (tgt, why)

# LONG hit stop. entry 100.0, now 99.8 => -20bps <= -15
tgt, why = scalp_decision([100.0, 100.0, 100.0, 99.8], +1, 100.0, 1, p)
assert tgt == 0 and "stop" in why.lower(), (tgt, why)

# time stop forces exit (move +1bps, not tp/stop; bars_held 5 >= 5)
tgt, why = scalp_decision([100.0, 100.0, 100.0, 100.05], -1, 100.06, 5, p)
assert tgt == 0 and "time" in why.lower(), (tgt, why)

# revert-to-mean exit: short entered just above mean, price back at mean.
# ema([100,100.05,100.0])=100.0125, price 100.0 -> dev -1.25bps <= exit_bps(2);
# profit from entry 100.05 is only +5bps (< tp 8), so it exits on REVERT not TP.
tgt, why = scalp_decision([100.0, 100.0, 100.05, 100.0], -1, 100.05, 1, p)
assert tgt == 0 and "revert" in why.lower(), (tgt, why)

# insufficient data -> hold current position
assert scalp_decision([100.0], 0, 0.0, 0, p)[0] == 0
print("OK")
