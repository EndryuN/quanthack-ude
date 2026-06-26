"""_ensure_stops must: reset a missing OR wrong-sided SL to the failsafe, CLEAR a
wrong-sided TP (left over from a direction flip — the bug that instant-closed a
human Force LONG over a short), and leave already-correct stops alone (no churn)."""
from bot.engine import Engine


class Pos:
    def __init__(self, symbol, is_long, price, sl, tp):
        self.symbol = symbol
        self.type = 0 if is_long else 1          # 0 = POSITION_TYPE_BUY
        self.price_current = price
        self.price_open = price
        self.sl = sl
        self.tp = tp


class _Res:
    retcode = 10009


class FakeClient:
    def __init__(self, positions): self._pos = positions; self.calls = []
    def positions(self, magic=None): return self._pos
    def sl_price(self, symbol, is_long, pct):
        p = next(x for x in self._pos if x.symbol == symbol)
        return round(p.price_current * (1 - pct), 2) if is_long else round(p.price_current * (1 + pct), 2)
    def set_sltp(self, symbol, sl, tp):
        self.calls.append((symbol, round(sl, 2), round(tp, 2))); return True, _Res()
    def set_sl(self, symbol, sl):
        self.calls.append((symbol, round(sl, 2), None)); return True, _Res()


def _engine(positions):
    e = Engine(dry_run=False)
    e.client = FakeClient(positions); e._overrides = {}
    e.cfg.use_control_panel = True
    return e


# Case 1: flipped LONG inherited the short's TP BELOW price -> must be cleared (the live bug)
e = _engine([Pos("BTCUSD", True, 59000.0, sl=50150.0, tp=58000.0)])
e._ensure_stops()
assert e.client.calls, "should act on the wrong-sided TP"
sym, sl, tp = e.client.calls[-1]
assert sym == "BTCUSD" and tp == 0.0, f"stale wrong-sided TP must be cleared, got tp={tp}"
assert sl < 59000.0, "SL stays below price for a long"
print("OK case1: wrong-sided TP cleared (no instant close)")

# Case 2: flipped LONG inherited the short's SL ABOVE price -> reset to failsafe below
e2 = _engine([Pos("BTCUSD", True, 59000.0, sl=67000.0, tp=0.0)])
e2._ensure_stops()
sym, sl, tp = e2.client.calls[-1]
assert sl < 59000.0, f"wrong-sided SL must reset below price, got sl={sl}"
print("OK case2: wrong-sided SL reset to failsafe")

# Case 3: correct SL, no TP -> NO action (idempotent, no churn)
e3 = _engine([Pos("EURUSD", True, 1.08, sl=1.02, tp=0.0)])
e3._ensure_stops()
assert not e3.client.calls, f"correct stops must not be re-sent, got {e3.client.calls}"
print("OK case3: correct stops untouched (no churn)")

# Case 4: missing SL -> failsafe attached
e4 = _engine([Pos("EURUSD", True, 1.08, sl=0.0, tp=0.0)])
e4._ensure_stops()
assert e4.client.calls and e4.client.calls[-1][1] < 1.08, "missing SL -> failsafe"
print("OK case4: missing SL attached")

# Case 5: SHORT with correct stops (SL above, TP below) -> untouched
e5 = _engine([Pos("BTCUSD", False, 59000.0, sl=67000.0, tp=58000.0)])
e5._ensure_stops()
assert not e5.client.calls, f"correct SHORT stops must be left alone, got {e5.client.calls}"
print("OK case5: correct short stops untouched")

print("OK")
