"""A live close that FAILS must not be recorded as a close (spec E0.7) — else
the decision log and stop-hit detection diverge from the real book. Fakes, no MT5."""
import io
from contextlib import redirect_stdout
from bot.engine import Engine


class P:
    def __init__(self, symbol, signed, profit=0.0):
        self.symbol = symbol; self.signed_lots = signed; self.profit = profit


class FakeClient:
    def __init__(self, pos, close_ok):
        self._pos = pos; self._close_ok = close_ok; self.closed = []
    def position_for(self, s): return self._pos.get(s)
    def close_symbol(self, s, comment=""):
        self.closed.append(s); return self._close_ok, None


def _reconcile(close_ok):
    e = Engine(dry_run=False)
    e.client = FakeClient({"EURUSD": P("EURUSD", 1.0, -20.0)}, close_ok=close_ok)
    buf = io.StringIO()
    with redirect_stdout(buf):
        orders = e._reconcile({"EURUSD": 0.0})   # want flat; currently long 1.0
    return e, orders


# failed close: the engine attempts it, but records NOTHING as closed
e, orders = _reconcile(close_ok=False)
assert e.client.closed == ["EURUSD"], "should have attempted the close"
assert orders == [], f"failed close must not be recorded, got {orders}"

# successful close: recorded as a close order
e, orders = _reconcile(close_ok=True)
assert len(orders) == 1 and orders[0]["side"] == "close" and orders[0]["symbol"] == "EURUSD", orders

print("OK")
