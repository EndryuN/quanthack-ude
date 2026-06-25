"""Applying a preset must trigger a reconcile that actually places orders to
reflect the new rules. Exercises the REAL _reconcile/_do_order path. No MT5."""
import json, os, tempfile, time
from bot.engine import Engine
from bot.risk import RiskSnapshot


class FakeAcct:
    equity = 1_000_000.0; margin = 0.0; margin_level = 900.0


class Res:
    retcode = 10009


class FakeClient:
    def __init__(self): self.orders = []
    def connect(self): pass
    def shutdown(self): pass
    def account(self): return FakeAcct()
    def positions(self, magic=None): return []
    def position_for(self, s): return None
    def market_order(self, s, lots, is_buy, comment="", sl=0.0, tp=0.0):
        self.orders.append((s, lots, is_buy)); return True, Res()


class FakeRisk:
    def assess(self, now=None):
        return RiskSnapshot(equity=1e6, used_margin=0, margin_level=900, margin_usage=0,
            gross_notional=0, gross_leverage=0, net_notional=0, net_directional_frac=0,
            largest_symbol="", largest_frac=0, allow_new_risk=True, kill=False, reasons=[])
    def enforce(self, snap, dry): return None


def _write(d, **kw):
    base = {"paused": False, "aggression": None, "block": [], "allow": None,
            "active_preset": "Default", "flatten_seq": 0, "apply_seq": 0, "refresh_seq": 0,
            "scalper_symbols": [], "scalper_armed": False}
    base.update(kw); json.dump(base, open(os.path.join(d, "controls.json"), "w"))


d = tempfile.mkdtemp()
_write(d, apply_seq=1, aggression=1.0)        # operator applied the Finals preset
e = Engine(dry_run=False); e.cfg.use_control_panel = True; e.cfg.state_dir = d
e.client = FakeClient(); e.risk = FakeRisk(); e.logger = None
e.cfg.use_trailing_stop = False; e.cfg.use_broker_sl = False
e._views = {"EURUSD": (1, 0.5)}; e._views_ts = time.time(); e._last_acted_ts = e._views_ts
e._build_targets = lambda acct: {"EURUSD": 5.0}   # the preset would size this target

e.tick()   # apply_seq > ack -> force a reconcile NEXT tick (this tick just acks)
assert e.client.orders == [], "apply tick acks; reconcile happens next tick"
ack = json.load(open(os.path.join(d, "control_ack.json")))
assert ack["apply_seq_done"] == 1

e.tick()   # now the reconcile runs on the new preset
assert e.client.orders and e.client.orders[0][0] == "EURUSD", \
    f"preset apply must reconcile -> order; got {e.client.orders}"

print("OK")
