"""Step-driven Scalper (integration rework). FakeClient, no MT5. Covers new-bar
gating, per-bar time-stop, broker-deal loss halt, foreign-symbol guard, new-risk
gating, broker TP+SL on entry, and fail-closed gating."""
import io
import numpy as np
from contextlib import redirect_stdout

from bot.scalper import Scalper
from bot.config import ScalperConfig

OWN = 990624


class P:
    def __init__(self, symbol, vol, typ, price_open=100.0, profit=0.0, magic=OWN):
        self.symbol = symbol; self.volume = vol; self.type = typ
        self.price_open = price_open; self.profit = profit; self.magic = magic
        self.ticket = id(self)


class FakeClient:
    def __init__(self, notime=False):
        self._pos = []; self.orders = []; self.closed = []; self.sltp = []
        self._closes = {}; self._time = {}; self._realized = 0.0; self._notime = notime
    def positions(self, magic=OWN):
        if magic is None:
            return list(self._pos)
        return [p for p in self._pos if getattr(p, "magic", OWN) == magic]
    def position_for(self, s):
        return next((p for p in self._pos if p.symbol == s and getattr(p, "magic", OWN) == OWN), None)
    def bars(self, s, tf, n):
        c = self._closes.get(s, [100.0] * n)
        if self._notime:
            rec = np.empty(len(c), dtype=[("close", "f8")]); rec["close"] = c
            return rec
        t0 = self._time.get(s, 0)
        rec = np.empty(len(c), dtype=[("close", "f8"), ("time", "i8")])
        rec["close"] = c; rec["time"] = list(range(t0, t0 + len(c)))
        return rec
    def market_order(self, s, lots, is_buy, comment="", sl=0.0, tp=0.0):
        self.orders.append({"symbol": s, "lots": lots, "is_buy": is_buy, "sl": sl, "tp": tp})
        return True, None
    def close_symbol(self, s, comment=""):
        self.closed.append(s); self._pos = [p for p in self._pos if p.symbol != s]
        return True, None
    def set_sltp(self, s, sl, tp): self.sltp.append((s, sl, tp)); return True, None
    def realized_profit(self, magic=OWN, days=None): return self._realized


CFG = ScalperConfig(enabled=True, symbols=["EURUSD"], lots={"EURUSD": 0.5},
                    ema_period=3, entry_bps=10.0, tp_bps=4.0, stop_bps=8.0,
                    time_stop_bars=3, max_concurrent=1, daily_loss_limit=500.0)
FADE_SHORT = [100.0, 100.0, 100.0, 100.5, 100.5]   # completed[-1]=100.5 >> EMA -> fade short
HOLD_SHORT = [100.0, 100.0, 100.0, 100.05, 100.05]  # short held: not reverted, within tp/stop


def fade_client():
    fc = FakeClient(); fc._closes["EURUSD"] = FADE_SHORT; return fc


# 1) dry-run fade entry sends nothing
s = Scalper(CFG, client=fade_client(), dry_run=True); s.step()
assert s.client.orders == [], "dry-run must not send"

# 2) live fade entry -> market_order SHORT with BOTH broker SL and TP (fast exits)
s = Scalper(CFG, client=fade_client(), dry_run=False); s.step()
assert len(s.client.orders) == 1
o = s.client.orders[0]
assert o["is_buy"] is False and o["sl"] > 0 and o["tp"] > 0, o   # short, server-side SL+TP

# 3) new-bar gating + per-bar time-stop on a held short position
fc = FakeClient(); fc._closes["EURUSD"] = HOLD_SHORT
fc._pos = [P("EURUSD", 0.5, 1, price_open=100.0)]           # short, magic OWN
s = Scalper(CFG, client=fc, dry_run=False)
fc._time["EURUSD"] = 0;  s.step()                            # first sight, bars_held=0
fc._time["EURUSD"] = 0;  s.step()                            # SAME forming bar -> gated, no-op
assert fc.closed == [], "same-bar re-poll must not act"
fc._time["EURUSD"] = 10; s.step()                            # new bar -> bars_held=1
fc._time["EURUSD"] = 20; s.step()                            # bars_held=2
assert fc.closed == [], "should still hold before time-stop"
fc._time["EURUSD"] = 30; s.step()                            # bars_held=3 == time_stop -> close
assert "EURUSD" in fc.closed, "time-stop should fire at time_stop_bars"

# 4) daily-loss halt sourced from BROKER deal history (not self-accumulation)
fc = FakeClient(); fc._realized = -600.0                     # past the 500 limit
fc._pos = [P("EURUSD", 0.5, 1)]
s = Scalper(CFG, client=fc, dry_run=False)
out = io.StringIO()
with redirect_stdout(out):
    s.step()
assert s._halted is True and "EURUSD" in fc.closed and "HALT" in out.getvalue().upper()

# 5) foreign-symbol guard: a position under a DIFFERENT magic -> stand down
fc = fade_client(); fc._pos = [P("EURUSD", 2.0, 0, magic=990623)]   # engine holds it
s = Scalper(CFG, client=fc, dry_run=False)
with redirect_stdout(io.StringIO()):
    s.step()
assert fc.orders == [], "must not open onto a foreign-magic symbol"

# 6) new-risk gating: allow_new_risk=False blocks opens
s = Scalper(CFG, client=fade_client(), dry_run=False); s.step(allow_new_risk=False)
assert s.client.orders == [], "must not open when new risk is blocked"

# 7) fail-closed: no bar timestamp -> no action (no per-loop over-trading)
fc = FakeClient(notime=True); fc._closes["EURUSD"] = FADE_SHORT
s = Scalper(CFG, client=fc, dry_run=False); s.step()
assert fc.orders == [], "missing bar timestamp must fail closed"

print("OK")
