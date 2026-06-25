"""Per-tick scalper slot (Phase 2). Slot mechanics tested with an injected stub
decision (strategy-independent), plus a crypto-default sanity check. No MT5."""
import io
import numpy as np
from contextlib import redirect_stdout

from bot.scalper import Scalper
from bot.config import ScalperConfig

OWN = 990624


class P:
    def __init__(self, symbol, vol, typ, profit=0.0, magic=OWN, sl=1.0, price_open=100.0):
        self.symbol = symbol; self.volume = vol; self.type = typ
        self.profit = profit; self.magic = magic; self.ticket = id(self)
        self.sl = sl; self.price_open = price_open    # sl=1.0 default -> "already protected"


class FakeClient:
    def __init__(self):
        self._pos = []; self.orders = []; self.closed = []; self.sltp = []
        self._closes = {}; self._realized = 0.0
        self._fill = 60000.0; self.order_ok = True; self.sltp_ok = True; self._lots = 1.0
    def positions(self, magic=OWN):
        return list(self._pos) if magic is None else [p for p in self._pos if getattr(p, "magic", OWN) == magic]
    def position_for(self, s):
        return next((p for p in self._pos if p.symbol == s and getattr(p, "magic", OWN) == OWN), None)
    def bars(self, s, tf, n):
        c = self._closes.get(s, [100.0] * max(n, 2))
        rec = np.empty(len(c), dtype=[("close", "f8")]); rec["close"] = c; return rec
    def market_order(self, s, lots, is_buy, comment="", sl=0.0, tp=0.0):
        self.orders.append({"symbol": s, "is_buy": is_buy, "sl": sl, "tp": tp, "lots": lots})
        if self.order_ok:    # a fill creates a position with NO stop yet
            self._pos.append(P(s, lots, 0 if is_buy else 1, magic=OWN, sl=0.0, price_open=self._fill))
        return self.order_ok, None
    def close_symbol(self, s, comment=""):
        self.closed.append(s); self._pos = [p for p in self._pos if p.symbol != s]; return True, None
    def set_sltp(self, s, sl, tp):
        self.sltp.append((s, sl, tp))
        if self.sltp_ok:
            for p in self._pos:
                if p.symbol == s: p.sl = sl; p.tp = tp
        return self.sltp_ok, None
    def realized_profit(self, magic=OWN, days=None): return self._realized
    def lots_for_notional(self, symbol, usd): return self._lots
    def clamp_stop_price(self, symbol, ref_price, desired, side):
        if ref_price <= 0 or desired <= 0: return 0.0
        desired = min(desired, ref_price) if side == "below" else max(desired, ref_price)
        return round(desired, 5)


CFG = ScalperConfig(enabled=True, symbols=["BTCUSD"], lots={"BTCUSD": 1.0},
                    tp_bps=20.0, stop_bps=20.0, daily_loss_limit=500.0, max_concurrent=1)
LONG = lambda sym, closes, cur, btc: (1, "stub long")
SHORT = lambda sym, closes, cur, btc: (-1, "stub short")


def fc_flat():
    fc = FakeClient(); fc._closes["BTCUSD"] = [60000.0] * 30; return fc


# 1) dry-run: a long signal sends nothing
s = Scalper(CFG, client=fc_flat(), dry_run=True, decide=LONG); s.step()
assert s.client.orders == []

# 2) live open: order carries NO inline SL/TP (broker rejects, 10006); the
#    server-side SL (below) + TP (above) are attached via the SLTP action post-fill
fc = fc_flat(); s = Scalper(CFG, client=fc, dry_run=False, decide=LONG); s.step()
assert len(fc.orders) == 1 and fc.orders[0]["is_buy"] is True
assert fc.orders[0]["sl"] == 0 and fc.orders[0]["tp"] == 0, fc.orders[0]
assert len(fc.sltp) == 1
sym, sl, tp = fc.sltp[0]
assert sym == "BTCUSD" and tp > 60000 and sl < 60000, fc.sltp[0]

# 3) new-risk gating: blocked -> no open
fc = fc_flat(); s = Scalper(CFG, client=fc, dry_run=False, decide=LONG); s.step(allow_new_risk=False)
assert fc.orders == []

# 4) foreign-symbol guard: a different magic holds BTCUSD -> stand down
fc = fc_flat(); fc._pos = [P("BTCUSD", 2.0, 0, magic=990623)]
s = Scalper(CFG, client=fc, dry_run=False, decide=LONG)
with redirect_stdout(io.StringIO()): s.step()
assert fc.orders == []

# 5) daily-loss halt from BROKER deal history
fc = fc_flat(); fc._realized = -600.0; fc._pos = [P("BTCUSD", 1.0, 0)]
s = Scalper(CFG, client=fc, dry_run=False, decide=LONG)
out = io.StringIO()
with redirect_stdout(out): s.step()
assert s._halted and "BTCUSD" in fc.closed and "HALT" in out.getvalue().upper()

# 6) reverse-breakout flip: holding long, signal flips short -> close
fc = fc_flat(); fc._pos = [P("BTCUSD", 1.0, 0)]    # long, own magic
s = Scalper(CFG, client=fc, dry_run=False, decide=SHORT); s.step()
assert "BTCUSD" in fc.closed

# 7) default crypto strategy: a rising BTC series opens long (no stub)
fc = FakeClient(); fc._closes["BTCUSD"] = [60000.0 * (1.001 ** i) for i in range(30)]
s = Scalper(CFG, client=fc, dry_run=False); s.step()
assert len(fc.orders) == 1 and fc.orders[0]["is_buy"] is True

# 8) C1: if the stop can't be attached (set_sltp rejected), the position is CLOSED
#    rather than left naked
fc = fc_flat(); fc.sltp_ok = False
s = Scalper(CFG, client=fc, dry_run=False, decide=LONG)
with redirect_stdout(io.StringIO()): s.step()
assert len(fc.orders) == 1 and "BTCUSD" in fc.closed, (fc.orders, fc.closed)

# 8b) §5 sizing: a symbol that sizes to 0 lots (no budget) is NOT opened
fc = fc_flat(); fc._lots = 0.0
s = Scalper(CFG, client=fc, dry_run=False, decide=LONG)
with redirect_stdout(io.StringIO()): s.step()
assert fc.orders == [], "unsized symbol must not open"

# 8c) §4 turnover cap: at max_trades_per_hour for the symbol -> no new open
import time as _t
fc = fc_flat()
s = Scalper(CFG, client=fc, dry_run=False, decide=LONG)
s._trades["BTCUSD"] = [_t.time()] * CFG.max_trades_per_hour   # already at the cap this hour
with redirect_stdout(io.StringIO()): s.step()
assert fc.orders == [], "trade cap must block new opens"

# 8d) §4 min-hold: a flip is blocked within min_hold_secs of opening
fc = fc_flat(); fc._pos = [P("BTCUSD", 1.0, 0)]              # long, own magic
s = Scalper(CFG, client=fc, dry_run=False, decide=SHORT)
s._opened_at["BTCUSD"] = _t.time()                           # just opened -> within min-hold
with redirect_stdout(io.StringIO()): s.step()
assert "BTCUSD" not in fc.closed, "min-hold must block an immediate flip"

# 9) C1 sweep: a pre-existing scalper position with NO stop gets protected
fc = fc_flat(); fc._pos = [P("BTCUSD", 1.0, 0, sl=0.0, price_open=60000.0)]
s = Scalper(CFG, client=fc, dry_run=False, decide=lambda *a: (1, "hold"))  # already long -> no new order
with redirect_stdout(io.StringIO()): s.step()
assert any(t[0] == "BTCUSD" for t in fc.sltp), "naked scalper position must be swept a stop"

print("OK")
