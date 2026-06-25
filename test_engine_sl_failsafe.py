"""An established position must never be left without its broker stop (spec E0.9).
If the SL can't be priced pre-trade (no quote), the engine retries via set_sl
post-fill and warns loudly if it still can't attach. Fakes, no MT5."""
import io
from contextlib import redirect_stdout
from bot.engine import Engine


class FakeClient:
    def __init__(self, sl_prices, order_ok=True, setsl_ok=True):
        self._sl = list(sl_prices)
        self.order_ok = order_ok; self.setsl_ok = setsl_ok
        self.orders = []; self.set_sl_calls = []
    def sl_price(self, symbol, is_buy, pct):
        return self._sl.pop(0) if self._sl else 0.0
    def market_order(self, symbol, lots, is_buy, comment="", sl=0.0):
        self.orders.append((symbol, lots, is_buy, sl)); return self.order_ok, None
    def set_sl(self, symbol, sl_price):
        self.set_sl_calls.append((symbol, sl_price)); return self.setsl_ok, None


def _open(sl_prices, **kw):
    e = Engine(dry_run=False); e.client = FakeClient(sl_prices, **kw)
    buf = io.StringIO()
    with redirect_stdout(buf):
        sl = e._do_order("EURUSD", 1.0, is_buy=True, cur=0.0, target=1.0)
    return e, sl, buf.getvalue()


# A) normal: SL priced pre-trade, attached inline, no retry needed
e, sl, _ = _open([1.5000])
assert e.client.orders[0][3] == 1.5000 and sl == 1.5000
assert e.client.set_sl_calls == []

# B) no quote pre-trade, recovers post-fill: order goes SL-less, then set_sl attaches
e, sl, out = _open([0.0, 1.4321])
assert e.client.orders[0][3] == 0.0, "opened without inline SL"
assert e.client.set_sl_calls == [("EURUSD", 1.4321)] and sl == 1.4321

# C) still no quote: cannot attach -> loud warning, returns no SL
e, sl, out = _open([0.0, 0.0])
assert e.client.set_sl_calls == [] and sl == 0.0
assert "WITHOUT STOP-LOSS" in out, out

# D) E1.6: a same-direction ADD must NOT attach an SL (would overwrite/loosen the
#    trailed stop on the netting position). The trail owns the stop after entry.
e = Engine(dry_run=False); e.client = FakeClient([1.5000])
buf = io.StringIO()
with redirect_stdout(buf):
    sl = e._do_order("EURUSD", 1.0, is_buy=True, cur=1.0, target=2.0)   # adding to a long
assert e.client.orders[0][3] == 0.0 and sl == 0.0, "add must not carry an SL"
assert e.client.set_sl_calls == []

# E) a FLIP into the opposite direction DOES attach a fresh SL (new position)
e = Engine(dry_run=False); e.client = FakeClient([0.9000])
with redirect_stdout(io.StringIO()):
    sl = e._do_order("EURUSD", 3.0, is_buy=False, cur=2.0, target=-1.0)  # long -> short
assert e.client.orders[0][3] == 0.9000 and sl == 0.9000, "flip should attach an SL"

print("OK")
