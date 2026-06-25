"""Every open position must have a broker stop — liquidation = elimination. The
ensure-sweep attaches the SL_PCT failsafe to any naked position. Fakes, no MT5."""
import io
from contextlib import redirect_stdout
from bot.engine import Engine


class Pos:                       # MT5 POSITION_TYPE_BUY=0, SELL=1
    def __init__(self, symbol, typ, sl=0.0, tp=0.0):
        self.symbol = symbol; self.type = typ; self.sl = sl; self.tp = tp; self.ticket = id(self)


class Res:
    def __init__(self, rc): self.retcode = rc


class FakeClient:
    def __init__(self, positions, sl_results=None):
        self._pos = positions; self.set_sl_calls = []; self.set_sltp_calls = []
        self._sl_results = list(sl_results or [])   # retcodes for successive set_sl calls
    def positions(self): return list(self._pos)
    def sl_price(self, symbol, is_buy, pct): return 95.0 if is_buy else 105.0   # nonzero failsafe
    def set_sl(self, symbol, sl):
        self.set_sl_calls.append(symbol)
        rc = self._sl_results.pop(0) if self._sl_results else 10009
        return rc == 10009, Res(rc)
    def set_sltp(self, symbol, sl, tp): self.set_sltp_calls.append(symbol); return True, None


def _eng(positions, panel=False, dry=False):
    e = Engine(dry_run=dry); e.cfg.use_broker_sl = True; e.cfg.use_control_panel = panel
    e._overrides = None; e.client = FakeClient(positions); return e


# naked positions get the failsafe; protected ones are left alone
e = _eng([Pos("BTCUSD", 1, sl=0.0), Pos("USDJPY", 0, sl=153.7), Pos("ETHUSD", 1, sl=0.0)])
with redirect_stdout(io.StringIO()): e._ensure_stops()
assert sorted(e.client.set_sl_calls) == ["BTCUSD", "ETHUSD"], e.client.set_sl_calls
assert "USDJPY" not in e.client.set_sl_calls

# control-panel path uses set_sltp (preserves any TP)
e2 = _eng([Pos("SOLUSD", 1, sl=0.0)], panel=True)
with redirect_stdout(io.StringIO()): e2._ensure_stops()
assert e2.client.set_sltp_calls == ["SOLUSD"] and e2.client.set_sl_calls == []

# operator-pinned positions are left to the operator
e3 = _eng([Pos("XAUUSD", 0, sl=0.0)]); e3._overrides = {"XAUUSD": {"pinned": True}}
with redirect_stdout(io.StringIO()): e3._ensure_stops()
assert e3.client.set_sl_calls == []

# dry-run sends nothing
e4 = _eng([Pos("BTCUSD", 1, sl=0.0)], dry=True)
with redirect_stdout(io.StringIO()): e4._ensure_stops()
assert e4.client.set_sl_calls == [] and e4.client.set_sltp_calls == []

# H2: a transient reject (10004) is retried in-tick (one retry -> 2 set_sl calls)
e5 = Engine(dry_run=False); e5.cfg.use_broker_sl = True; e5.cfg.use_control_panel = False
e5._overrides = None
e5.client = FakeClient([Pos("BTCUSD", 1, sl=0.0)], sl_results=[10004, 10009])
with redirect_stdout(io.StringIO()): e5._ensure_stops()
assert e5.client.set_sl_calls == ["BTCUSD", "BTCUSD"], e5.client.set_sl_calls

# H1: refuse to start live with use_broker_sl OFF (positions would run naked)
e6 = Engine(dry_run=False); e6.cfg.use_broker_sl = False
try:
    e6.start(); raise AssertionError("should refuse to start live without stops")
except RuntimeError as ex:
    assert "use_broker_sl" in str(ex)

print("OK")
