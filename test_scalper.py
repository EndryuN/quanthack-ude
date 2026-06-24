"""Scalper risk gates + dry-run, with a FakeClient. No MT5."""
import io
from contextlib import redirect_stdout
from bot.scalper import Scalper
from bot.config import ScalperConfig


class P:
    def __init__(self, symbol, vol, typ, profit=0.0):
        self.ticket = id(self); self.symbol = symbol; self.volume = vol
        self.type = typ; self.profit = profit
        self.signed_lots = vol if typ == 0 else -vol


class FakeClient:
    def __init__(self):
        self._pos = []; self.orders = []; self.closed = []
        self._closes = {}
    def positions(self, magic=None): return list(self._pos)
    def position_for(self, s): return next((p for p in self._pos if p.symbol == s), None)
    def bars(self, s, tf, n):
        import numpy as np
        c = self._closes.get(s, [100.0] * n)
        rec = np.empty(len(c), dtype=[("close", "f8")]); rec["close"] = c
        return rec
    def market_order(self, s, lots, is_buy, comment="", sl=0.0):
        self.orders.append((s, lots, is_buy)); return True, None
    def close_symbol(self, s, comment=""):
        self.closed.append(s); return True, None


cfg = ScalperConfig(enabled=True, symbols=["EURUSD"], lots={"EURUSD": 0.5},
                    ema_period=3, entry_bps=10.0, max_concurrent=1, daily_loss_limit=500.0)

# dry-run: a fade signal must NOT place a real order
s = Scalper(cfg, dry_run=True); s.client = FakeClient()
s.client._closes["EURUSD"] = [100.0, 100.0, 100.0, 100.3]   # +15bps -> fade short
s.tick_once()
assert s.client.orders == [], "dry-run must send nothing"

# live: same signal opens a (short) position
s2 = Scalper(cfg, dry_run=False); s2.client = FakeClient()
s2.client._closes["EURUSD"] = [100.0, 100.0, 100.0, 100.3]
s2.tick_once()
assert len(s2.client.orders) == 1 and s2.client.orders[0][2] is False  # is_buy False = short

# daily-loss kill-switch: realized loss past the limit -> flatten + halt
s3 = Scalper(cfg, dry_run=False); s3.client = FakeClient()
s3._realized_pnl = -600.0
s3.client._pos = [P("EURUSD", 0.5, 1)]
out = io.StringIO()
with redirect_stdout(out):
    s3.tick_once()
assert "EURUSD" in s3.client.closed, "kill-switch should flatten"
assert s3._halted is True
assert "HALT" in out.getvalue().upper()
# once halted, no new orders even on a signal
s3.client.orders.clear(); s3.client._closes["EURUSD"] = [100.0, 100.0, 100.0, 100.3]
s3.tick_once()
assert s3.client.orders == []
print("OK")
