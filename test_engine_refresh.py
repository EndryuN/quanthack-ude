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
    # stub the async refresh path (no MT5 / no network); record each ATTEMPT
    preps = []
    e._refresh_prep = lambda: (preps.append(1) or {"context": "x", "symbols": ["EURUSD"]})
    e._refresh_worker = lambda prep: _ok()
    e._score_models = lambda *a, **k: None
    e._build_targets = lambda acct: {}            # avoid the sizer/MT5 path on reconcile
    return e, preps


def _ok():
    return {"ok": True, "sizer_views": {"EURUSD": (1, 0.5)}, "view_records": [{"symbol": "EURUSD"}],
            "regime": "r", "claude_views": {"EURUSD": (1, 0.5)}, "nemo_views": {}, "extra_voters": {},
            "claude_usage": {"in_tokens": 1, "out_tokens": 1, "usd": 0.0},
            "nemo_usage": None, "nemo_regime": None, "ens_raw": {}}


# baseline: fresh views + within backoff -> no refresh attempt
d = tempfile.mkdtemp(); _write(d)
e, preps = _engine(d); e.tick()
assert preps == [], "should not refresh fresh views within backoff"
assert e._refresh_thread is None

# refresh_seq bumped -> forces a refresh attempt despite fresh views + backoff
d = tempfile.mkdtemp(); _write(d, refresh_seq=1)
e, preps = _engine(d); e.tick()
assert preps == [1], "refresh_seq must force an AI refresh"
assert json.load(open(os.path.join(d, "control_ack.json")))["refresh_seq_done"] == 1
assert e._force_refresh is False                  # one-shot cleared once attempted
if e._refresh_thread is not None: e._refresh_thread.join(timeout=5)
e.tick()                                          # applies result; same seq -> no NEW attempt
assert preps == [1], "one-shot: does not repeat"

# a force pressed while a refresh is IN FLIGHT must NOT be dropped
import threading
d = tempfile.mkdtemp(); _write(d, refresh_seq=1)
e, preps = _engine(d)
rel = threading.Event()
e._refresh_worker = lambda prep: (rel.wait(10), _ok())[1]   # block until released
e.tick()                                          # launches the (blocked) worker
assert preps == [1] and e._refresh_thread.is_alive()
e._force_refresh = True                            # operator presses refresh mid-flight
e.tick()                                           # in flight -> no second launch, no drop
assert preps == [1], "no second launch while in flight"
assert e._force_refresh is True, "a mid-flight force must stay pending, not be swallowed"
rel.set(); e._refresh_thread.join(timeout=10)
e.tick()                                           # applies result + honors the pending force
assert len(preps) == 2, "pending force fires once the in-flight refresh completes"

print("OK")
