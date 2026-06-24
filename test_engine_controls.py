"""Gated control-channel wiring. Uses fakes so it needs no MT5 and sends nothing.
Proves: (A) kill-switch runs BEFORE controls (risk floor is never gated),
(B) flatten one-shot, (C) apply one-shot forces reconcile, (D) flag-off = no-op."""
import json, os, tempfile, time
from bot.engine import Engine
from bot.risk import RiskSnapshot


class FakeAcct:
    equity = 1_000_000.0; margin = 0.0; margin_level = 900.0


class FakeClient:
    def __init__(self): self.flattened = 0
    def connect(self): pass
    def shutdown(self): pass
    def account(self): return FakeAcct()
    def positions(self): return []
    def position_for(self, s): return None
    def flatten_all(self, comment=""): self.flattened += 1; return []


def _snap(kill=False):
    return RiskSnapshot(equity=1_000_000.0, used_margin=0.0, margin_level=900.0,
        margin_usage=0.0, gross_notional=0.0, gross_leverage=0.0, net_notional=0.0,
        net_directional_frac=0.0, largest_symbol="", largest_frac=0.0,
        allow_new_risk=True, kill=kill, reasons=[])


class FakeRisk:
    def __init__(self, kill=False): self._kill = kill; self.enforced = 0
    def assess(self, now=None): return _snap(self._kill)
    def enforce(self, snap, dry): self.enforced += 1; return "FLATTENED (kill)"


def _engine(state_dir, kill=False):
    e = Engine(dry_run=True)
    e.cfg.use_control_panel = True
    e.cfg.state_dir = state_dir
    e.client = FakeClient()
    e.risk = FakeRisk(kill=kill)
    # neutralize the AI path so tick() doesn't call out: fresh views, already acted
    e._views = {}; e._views_ts = time.time(); e._last_acted_ts = e._views_ts
    e.cfg.use_trailing_stop = False
    e.logger = None
    return e


def _write(d, **kw):
    base = {"paused": False, "aggression": None, "block": [], "allow": None,
            "active_preset": "Default", "flatten_seq": 0, "apply_seq": 0}
    base.update(kw)
    json.dump(base, open(os.path.join(d, "controls.json"), "w"))


# A) kill-switch precedes controls: even with a paused controls file, a kill tick
#    flattens via risk.enforce and NEVER loads controls.
d = tempfile.mkdtemp(); _write(d, paused=True)
e = _engine(d, kill=True); e.tick()
assert e.risk.enforced == 1, "kill-switch enforced"
assert e._controls is None, "controls never loaded on a kill tick"

# B) operator flatten one-shot: flatten_seq bump -> flatten_all + ack updated
d = tempfile.mkdtemp(); _write(d, flatten_seq=1)
e = _engine(d); e.tick()
assert e.client.flattened == 1, "operator flatten ran"
ack = json.load(open(os.path.join(d, "control_ack.json")))
assert ack["flatten_seq_done"] == 1
e.tick()  # same seq -> must NOT flatten again
assert e.client.flattened == 1, "one-shot: flatten does not repeat"

# C) apply one-shot: apply_seq bump -> forces reconcile (resets _last_acted_ts) + ack
d = tempfile.mkdtemp(); _write(d, apply_seq=1)
e = _engine(d); before = e._last_acted_ts; e.tick()
ack = json.load(open(os.path.join(d, "control_ack.json")))
assert ack["apply_seq_done"] == 1
assert e._last_acted_ts != before, "apply forced a reconcile (last_acted reset)"

# D) flag OFF: controls.json present but ignored, _controls stays None
d = tempfile.mkdtemp(); _write(d, paused=True, flatten_seq=9)
e = _engine(d); e.cfg.use_control_panel = False; e.tick()
assert e._controls is None and e.client.flattened == 0, "flag off = no control reads"

print("OK")
