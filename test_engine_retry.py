"""Transient order failures are re-quoted and retried; non-transient ones are
not (spec E0.6). Fakes, no MT5."""
import io
from contextlib import redirect_stdout
from bot.engine import Engine

DONE, REQUOTE, REJECT = 10009, 10004, 10006   # success / transient / hard-reject


class Res:
    def __init__(self, rc): self.retcode = rc


class FakeClient:
    def __init__(self, order_rcs=(), close_rcs=()):
        self._o = list(order_rcs); self._c = list(close_rcs)
        self.order_calls = 0; self.close_calls = 0
    def sl_price(self, *a): return 0.0
    def market_order(self, symbol, lots, is_buy, comment="", sl=0.0):
        self.order_calls += 1; rc = self._o.pop(0); return (rc == DONE, Res(rc))
    def close_symbol(self, symbol, comment=""):
        self.close_calls += 1; rc = self._c.pop(0); return (rc == DONE, Res(rc))


def _engine(order_rcs=(), close_rcs=()):
    e = Engine(dry_run=False); e.cfg.use_broker_sl = False
    e.client = FakeClient(order_rcs, close_rcs)
    return e


def _do_order(e):
    with redirect_stdout(io.StringIO()):
        e._do_order("EURUSD", 1.0, is_buy=True, cur=0.0, target=1.0)


# A) transient then success -> retried once, total 2 attempts
e = _engine(order_rcs=[REQUOTE, DONE]); _do_order(e)
assert e.client.order_calls == 2

# B) hard reject -> NOT retried
e = _engine(order_rcs=[REJECT]); _do_order(e)
assert e.client.order_calls == 1

# C) persistent transient -> capped at _MAX_ORDER_ATTEMPTS (3)
e = _engine(order_rcs=[REQUOTE, REQUOTE, REQUOTE]); _do_order(e)
assert e.client.order_calls == 3

# D) close: transient then success -> retried, returns True
e = _engine(close_rcs=[REQUOTE, DONE])
with redirect_stdout(io.StringIO()):
    ok = e._do_close("EURUSD")
assert ok is True and e.client.close_calls == 2

print("OK")
