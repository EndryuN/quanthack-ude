"""On launch the engine resets the scalper session so a persisted armed/running
state never auto-resumes (the operator re-does assign -> gate -> arm). No MT5."""
import json, os, tempfile
from bot.engine import Engine
from bot.controls import load_controls


def _controls_path(d): return os.path.join(d, "controls.json")


# a previous session left the scalper ARMED LIVE with an assignment + cleared gate
d = tempfile.mkdtemp()
json.dump({"scalper_symbols": ["BTCUSD"], "scalper_armed": True,
           "scalper_armed_force": True,
           "scalper_gate": {"BTCUSD": {"cleared": True}}},
          open(_controls_path(d), "w"))
# also a stale scalper.json from last run
json.dump({"symbols": [{"symbol": "BTCUSD", "state": "in_position"}]},
          open(os.path.join(d, "scalper.json"), "w"))

e = Engine(dry_run=True); e.cfg.use_control_panel = True; e.cfg.state_dir = d
e._reset_scalper_session()

c = load_controls(d)
assert c["scalper_symbols"] == [], c["scalper_symbols"]
assert c["scalper_armed"] is False and c["scalper_armed_force"] is False
assert c["scalper_gate"] == {}
assert json.load(open(os.path.join(d, "scalper.json")))["symbols"] == []

# non-control-panel mode is a no-op (nothing to reset)
d2 = tempfile.mkdtemp()
e2 = Engine(dry_run=True); e2.cfg.use_control_panel = False; e2.cfg.state_dir = d2
e2._reset_scalper_session()
assert not os.path.exists(_controls_path(d2))

print("OK")
