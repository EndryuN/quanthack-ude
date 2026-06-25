"""Trailing-stop math (spec E2.5): arms only after a meaningful favorable move,
then trails a TIGHTER distance to lock real profit, ratchets monotonically, and
never locks worse than break-even. Pure function, no MT5."""
from bot.engine import _trail_candidate

D = 5
ARM, TR = 0.010, 0.005   # arm at +1%, trail 0.5% behind

# LONG ---------------------------------------------------------------
# not armed until price is up by ARM
assert _trail_candidate(True, 1.0050, 1.0000, 0.0, ARM, TR, D) is None      # +0.5% < arm
# armed at +1.0%: locks REAL profit (above entry), not break-even
c = _trail_candidate(True, 1.0100, 1.0000, 0.0, ARM, TR, D)
assert c is not None and c == round(1.0100 * (1 - TR), D) and c > 1.0000
# ratchets UP only — a higher existing stop is never loosened
assert _trail_candidate(True, 1.0100, 1.0000, c + 0.01, ARM, TR, D) is None
# higher price moves the stop up
c2 = _trail_candidate(True, 1.0200, 1.0000, c, ARM, TR, D)
assert c2 is not None and c2 > c
# never locks <= break-even (tight arm, wide trail -> cand below entry -> None)
assert _trail_candidate(True, 1.0100, 1.0000, 0.0, 0.005, 0.020, D) is None

# SHORT --------------------------------------------------------------
assert _trail_candidate(False, 0.9950, 1.0000, 0.0, ARM, TR, D) is None     # -0.5% < arm
cs = _trail_candidate(False, 0.9900, 1.0000, 0.0, ARM, TR, D)
assert cs is not None and cs == round(0.9900 * (1 + TR), D) and cs < 1.0000
# ratchets DOWN only
assert _trail_candidate(False, 0.9900, 1.0000, cs - 0.01, ARM, TR, D) is None
cs2 = _trail_candidate(False, 0.9800, 1.0000, cs, ARM, TR, D)
assert cs2 is not None and cs2 < cs

print("OK")
