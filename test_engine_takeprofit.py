"""Gated take-profit application + flag-off no-op. Fakes, no MT5."""
import json, os, tempfile, time
from bot.engine import Engine
from bot.risk import RiskSnapshot


class FakeAcct:
    equity = 1e6; margin = 0.0; margin_level = 900.0


class FakePos:
    def __init__(self, symbol, sl=1.05, tp=0.0):
        self.symbol = symbol; self.signed_lots = 2.0; self.sl = sl; self.tp = tp
    type = 0; ticket = 1; price_open = 1.0; price_current = 1.0


class FakeClient:
    def __init__(self, pos_tp=0.0): self._tp = pos_tp; self.sltp_calls = []
    def connect(self): pass
    def shutdown(self): pass
    def account(self): return FakeAcct()
    def positions(self): return [FakePos("USDJPY", sl=153.0, tp=self._tp)]
    def position_for(self, s): return FakePos(s, sl=153.0, tp=self._tp)
    def set_sltp(self, symbol, sl, tp): self.sltp_calls.append((symbol, sl, tp)); return True, None


def _snap():
    return RiskSnapshot(equity=1e6, used_margin=0.0, margin_level=900.0, margin_usage=0.0,
        gross_notional=0.0, gross_leverage=0.0, net_notional=0.0, net_directional_frac=0.0,
        largest_symbol="", largest_frac=0.0, allow_new_risk=True, kill=False, reasons=[])


class FakeRisk:
    def assess(self, now=None): return _snap()
    def enforce(self, snap, dry): return None


def _engine(d, flag=True, dry_run=True):
    e = Engine(dry_run=dry_run)
    e.cfg.use_control_panel = flag
    e.cfg.state_dir = d
    e.client = FakeClient()
    e.risk = FakeRisk()
    e._views = {}; e._views_ts = time.time(); e._last_acted_ts = e._views_ts  # no reconcile
    e.cfg.use_trailing_stop = False
    e.logger = None
    return e


# TP applied: a symbol with a take_profit override whose broker tp differs gets
# set_sltp. Run dry_run=False so the broker path is exercised (FakeClient sends nothing).
d = tempfile.mkdtemp()
json.dump({"USDJPY": {"take_profit": 154.5}}, open(os.path.join(d, "overrides.json"), "w"))
e = _engine(d, dry_run=False); e.tick()
assert ("USDJPY", 153.0, 154.5) in e.client.sltp_calls, "TP applied via set_sltp, SL preserved"

# Idempotent: if broker tp already equals the override, no redundant call.
d = tempfile.mkdtemp()
json.dump({"USDJPY": {"take_profit": 154.5}}, open(os.path.join(d, "overrides.json"), "w"))
e = _engine(d, dry_run=False); e.client = FakeClient(pos_tp=154.5); e.tick()
assert e.client.sltp_calls == [], "no redundant set_sltp when tp already set"

# Flag off: override present, no TP applied (dry-run default; flag off short-circuits).
d = tempfile.mkdtemp()
json.dump({"USDJPY": {"take_profit": 154.5}}, open(os.path.join(d, "overrides.json"), "w"))
e = _engine(d, flag=False); e.tick()
assert e.client.sltp_calls == [], "flag off: no TP application"
print("OK")
