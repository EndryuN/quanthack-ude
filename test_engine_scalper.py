"""Engine hosts the scalper slot (spec G1/§9.4): dynamic assignment cedes the
symbol from the engine's live universe, hands off the engine's position, steps the
scalper, and keeps it dry-run until armed. Fakes, no MT5."""
import json, os, tempfile, time
from types import SimpleNamespace
from bot.engine import Engine
from bot.risk import RiskSnapshot


class FakeAcct:
    equity = 1_000_000.0; margin = 0.0; margin_level = 900.0


class Pos:
    def __init__(self, symbol, signed): self.symbol = symbol; self.signed_lots = signed


class FakeClient:
    def __init__(self, positions): self._pos = dict(positions); self.closed = []
    def connect(self): pass
    def shutdown(self): pass
    def account(self): return FakeAcct()
    def positions(self, magic=None): return list(self._pos.values())
    def position_for(self, s): return self._pos.get(s)
    def close_symbol(self, s, comment=""):
        self.closed.append(s); self._pos.pop(s, None); return True, None
    def flatten_all(self, comment="", all_account=False): return []


class FakeRisk:
    def assess(self, now=None):
        return RiskSnapshot(equity=1e6, used_margin=0, margin_level=900, margin_usage=0,
            gross_notional=0, gross_leverage=0, net_notional=0, net_directional_frac=0,
            largest_symbol="", largest_frac=0, allow_new_risk=True, kill=False, reasons=[])
    def enforce(self, snap, dry): return None


class FakeScalper:
    def __init__(self): self.cfg = SimpleNamespace(symbols=[]); self.dry_run = True; self.steps = []
    def connect(self): pass
    def step(self, allow_new_risk=True): self.steps.append(allow_new_risk)


def _engine(d, **controls):
    e = Engine(dry_run=False)
    e.cfg.use_control_panel = True; e.cfg.state_dir = d
    e.client = FakeClient({"EURUSD": Pos("EURUSD", 2.0)})   # engine holds EURUSD long
    e.risk = FakeRisk()
    e._views = {}; e._views_ts = time.time(); e._last_acted_ts = e._views_ts
    e.cfg.use_trailing_stop = False; e.cfg.use_broker_sl = False; e.logger = None
    e._scalper = FakeScalper()                              # inject -> _run_scalper skips real connect
    base = {"paused": False, "aggression": None, "block": [], "allow": None,
            "active_preset": "Default", "flatten_seq": 0, "apply_seq": 0,
            "scalper_symbols": [], "scalper_armed": False}
    base.update(controls)
    json.dump(base, open(os.path.join(d, "controls.json"), "w"))
    return e


# assign EURUSD to the scalper (dry-run)
d = tempfile.mkdtemp(); e = _engine(d, scalper_symbols=["EURUSD"]); e.tick()
assert "EURUSD" not in e._active_symbols and "GBPUSD" in e._active_symbols, "symbol ceded live"
assert "EURUSD" in e.client.closed, "handoff flattened the engine's position"
assert e._scalper.cfg.symbols == ["EURUSD"] and e._scalper.steps == [True], "scalper stepped"
assert e._scalper.dry_run is True, "unarmed scalper forced dry-run"

# armed -> scalper runs live (engine itself is live)
d = tempfile.mkdtemp(); e = _engine(d, scalper_symbols=["EURUSD"], scalper_armed=True); e.tick()
assert e._scalper.dry_run is False, "armed scalper goes live"

# no assignment -> no handoff, no step, symbol stays with the engine
d = tempfile.mkdtemp(); e = _engine(d); e.tick()
assert e._scalper.steps == [] and "EURUSD" in e._active_symbols and "EURUSD" not in e.client.closed

print("OK")
