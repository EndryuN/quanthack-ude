"""Min-stop-distance clamp (spec C2): SL/TP are pushed to at least the broker's
trade_stops_level from the reference price, so tight bps levels aren't rejected
(10016). Stubs the mt5 module."""
import bot.client as clientmod
from bot.client import MT5Client


class Info:
    digits = 3
    point = 0.001
    trade_stops_level = 100   # min distance = 100 * 0.001 = 0.10 price units


class FakeMT5:
    def __init__(self, info=Info()): self._info = info
    def symbol_info(self, s): return self._info


clientmod.mt5 = FakeMT5()
c = MT5Client()

REF = 60.0   # e.g. a metals/quote price; min distance = 0.10
# desired SL only 0.02 below ref (inside the 0.10 minimum) -> pushed out below
sl = c.clamp_stop_price("XAGUSD", REF, REF - 0.02, "below")
assert sl <= REF - 0.10, sl                      # at least the min distance away
assert sl < REF                                  # correct side

# desired TP only 0.02 above ref -> pushed out above the minimum
tp = c.clamp_stop_price("XAGUSD", REF, REF + 0.02, "above")
assert tp >= REF + 0.10, tp
assert tp > REF

# a desired level already FAR enough is left as-is (just rounded)
far_sl = c.clamp_stop_price("XAGUSD", REF, REF - 5.0, "below")
assert abs(far_sl - 55.0) < 1e-9, far_sl

# rounded to the symbol's digits (3)
assert round(sl, 3) == sl

# min_stop_distance helper
assert abs(c.min_stop_distance("XAGUSD") - 0.10) < 1e-9

# no broker info / bad inputs -> 0.0 (caller treats as "couldn't compute")
clientmod.mt5 = FakeMT5(info=None)
assert c.clamp_stop_price("XAGUSD", REF, REF - 1, "below") == 0.0

print("OK")
