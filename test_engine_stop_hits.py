"""A position that vanishes between ticks WITHOUT an engine close is reported as
a broker stop hit; an engine-closed symbol is not. Fakes, no MT5."""
import io
from contextlib import redirect_stdout
from bot.engine import Engine


class P:
    def __init__(self, ticket, symbol, profit):
        self.ticket = ticket; self.symbol = symbol; self.profit = profit


class FakeClient:
    def __init__(self, positions): self._p = positions
    def positions(self): return self._p


def _cap(fn):
    buf = io.StringIO()
    with redirect_stdout(buf):
        fn()
    return buf.getvalue()

e = Engine(dry_run=True)
# tick 1: two open positions get recorded
e.client = FakeClient([P(1, "XAGUSD", 300.0), P(2, "BTCUSD", -50.0)])
e._report_stop_hits(set())
assert e._last_positions[1]["symbol"] == "XAGUSD"

# tick 2: XAGUSD vanished (stop hit), BTCUSD still open -> report XAGUSD only
e.client = FakeClient([P(2, "BTCUSD", -40.0)])
out = _cap(lambda: e._report_stop_hits(set()))
assert "STOP HIT" in out and "XAGUSD" in out and "BTCUSD" not in out
assert "+300" in out.replace(",", "")   # the last-known profit is reported

# engine-closed symbol that vanishes is NOT a stop hit
e.client = FakeClient([P(3, "EURUSD", 10.0)])
e._report_stop_hits(set())                       # record EURUSD
e.client = FakeClient([])                         # EURUSD gone
out2 = _cap(lambda: e._report_stop_hits({"EURUSD"}))  # engine closed it
assert "STOP HIT" not in out2

# never raises if the client blows up
class Boom:
    def positions(self): raise RuntimeError("mt5 down")
e.client = Boom()
e._report_stop_hits(set())   # must not raise
print("OK")
