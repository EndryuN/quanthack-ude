"""_do_order opens WITHOUT an inline stop-loss — this broker rejects market DEALs
that carry one (ret 10006, observed live on metals). The stop is attached
separately by _ensure_stops (see test_engine_ensure_sl). Fakes, no MT5."""
import io
from contextlib import redirect_stdout
from bot.engine import Engine

DONE, REQUOTE = 10009, 10004


class Res:
    def __init__(self, rc): self.retcode = rc


class FakeClient:
    def __init__(self, rcs=(DONE,)):
        self._rcs = list(rcs); self.orders = []
    def market_order(self, symbol, lots, is_buy, comment="", sl=0.0, tp=0.0):
        self.orders.append({"symbol": symbol, "is_buy": is_buy, "sl": sl, "tp": tp})
        rc = self._rcs.pop(0) if self._rcs else DONE
        return rc == DONE, Res(rc)


def _do(rcs=(DONE,)):
    e = Engine(dry_run=False); e.cfg.use_broker_sl = True; e.client = FakeClient(rcs)
    with redirect_stdout(io.StringIO()):
        e._do_order("XAUUSD", 0.37, is_buy=False, cur=0.0, target=-1.0)
    return e


# the DEAL never carries an inline SL/TP (broker would reject it)
e = _do()
assert e.client.orders[0]["sl"] == 0.0 and e.client.orders[0]["tp"] == 0.0, e.client.orders[0]

# transient failure still re-quotes and retries (E0.6), still without inline SL
e = _do([REQUOTE, DONE])
assert len(e.client.orders) == 2
assert all(o["sl"] == 0.0 for o in e.client.orders)

print("OK")
