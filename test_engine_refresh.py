"""Operator 'refresh consensus now' (refresh_seq) forces an AI re-query this tick,
bypassing the 15-min cadence and the failure backoff. One-shot. Fakes, no MT5."""
import json, os, tempfile, time
from bot.engine import Engine
from bot.risk import RiskSnapshot


class FakeAcct:
    equity = 1_000_000.0; margin = 0.0; margin_level = 900.0


class FakeClient:
    def connect(self): pass
    def shutdown(self): pass
    def account(self): return FakeAcct()
    def positions(self, magic=None): return []
    def position_for(self, s): return None


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


def _engine(d):
    e = Engine(dry_run=True); e.cfg.use_control_panel = True; e.cfg.state_dir = d
    e.client = FakeClient(); e.risk = FakeRisk()
    e.cfg.use_trailing_stop = False; e.cfg.use_broker_sl = False; e.logger = None
    # views FRESH and backoff HIGH -> a normal tick would NOT refresh
    e._views = {"EURUSD": (1, 0.5)}; e._views_ts = time.time(); e._last_acted_ts = e._views_ts
    e._last_ai_attempt = time.time(); e._ai_backoff = 300.0
    calls = []; e._refresh_ai_views = lambda: calls.append(1)
    return e, calls


# baseline: fresh views + within backoff -> no refresh
d = tempfile.mkdtemp(); _write(d)
e, calls = _engine(d); e.tick()
assert calls == [], "should not refresh fresh views within backoff"

# refresh_seq bumped -> forces a refresh despite fresh views + backoff
d = tempfile.mkdtemp(); _write(d, refresh_seq=1)
e, calls = _engine(d); e.tick()
assert calls == [1], "refresh_seq must force an AI refresh"
assert json.load(open(os.path.join(d, "control_ack.json")))["refresh_seq_done"] == 1
assert e._force_refresh is False                  # one-shot cleared
e.tick()                                          # same seq -> no repeat
assert calls == [1], "one-shot: does not repeat"

print("OK")
