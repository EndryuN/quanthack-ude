"""Gated per-position overrides: PIN freeze + kill-switch-flattens-pinned
invariant + flag-off no-op. Uses fakes (no MT5)."""
import json, os, tempfile, time
from bot.engine import Engine
from bot.risk import RiskSnapshot


class FakeAcct:
    equity = 1_000_000.0; margin = 0.0; margin_level = 900.0


class FakePos:
    def __init__(self, symbol): self.symbol = symbol; self.signed_lots = 2.0
    type = 0; ticket = 1; sl = 0.0; tp = 0.0; price_open = 1.0; price_current = 1.0


class FakeClient:
    def __init__(self): self.flattened = 0; self.reconciled = []
    def connect(self): pass
    def shutdown(self): pass
    def account(self): return FakeAcct()
    def positions(self): return [FakePos("EURUSD")]
    def position_for(self, s): return FakePos(s)
    def flatten_all(self, comment=""): self.flattened += 1; return []


def _snap(kill=False):
    return RiskSnapshot(equity=1e6, used_margin=0.0, margin_level=900.0,
        margin_usage=0.0, gross_notional=0.0, gross_leverage=0.0, net_notional=0.0,
        net_directional_frac=0.0, largest_symbol="", largest_frac=0.0,
        allow_new_risk=True, kill=kill, reasons=[])


class FakeRisk:
    def __init__(self, kill=False): self._kill = kill; self.enforced = 0
    def assess(self, now=None): return _snap(self._kill)
    def enforce(self, snap, dry): self.enforced += 1; return "FLATTENED"


def _engine(state_dir, kill=False):
    e = Engine(dry_run=True)
    e.cfg.use_control_panel = True
    e.cfg.state_dir = state_dir
    e.client = FakeClient()
    e.risk = FakeRisk(kill=kill)
    e._views = {"EURUSD": (1, 0.5)}; e._views_ts = time.time()
    e._last_acted_ts = -1.0          # force reconcile
    e.cfg.use_trailing_stop = False
    e.logger = None
    return e


def _write_ov(d, ov):
    with open(os.path.join(d, "overrides.json"), "w") as f: json.dump(ov, f)


# PIN: a pinned symbol is skipped in _reconcile (we capture what reconcile sees).
d = tempfile.mkdtemp(); _write_ov(d, {"EURUSD": {"pinned": True}})
e = _engine(d)
seen = []
orig = e._reconcile
e._build_targets = lambda acct: {"EURUSD": 9.0}   # wants to trade EURUSD
e._reconcile = lambda targets: (seen.append(dict(targets)) or [])
e.tick()
assert "EURUSD" not in seen[0], "pinned EURUSD is skipped before reconcile"

# KILL-SWITCH FLATTENS PINNED: even pinned, a kill tick flattens via risk path.
d = tempfile.mkdtemp(); _write_ov(d, {"EURUSD": {"pinned": True}})
e = _engine(d, kill=True); e.tick()
assert e.risk.enforced == 1, "kill-switch flattens even a pinned position"
assert e._overrides is None, "overrides never loaded on a kill tick"

# FLAG OFF: overrides present but ignored.
d = tempfile.mkdtemp(); _write_ov(d, {"EURUSD": {"pinned": True}})
e = _engine(d); e.cfg.use_control_panel = False
e._build_targets = lambda acct: {"EURUSD": 9.0}
seen = []; e._reconcile = lambda targets: (seen.append(dict(targets)) or [])
e.tick()
assert "EURUSD" in seen[0] and e._overrides is None, "flag off: pin ignored"
print("OK")
