"""Pure control-channel logic — no MT5, no engine."""
import json, os, tempfile
from bot.controls import (DEFAULT_CONTROLS, DEFAULT_PRESETS, load_controls, load_ack,
                          atomic_write_json, derisk_clamp, blocked_set, effective_aggression)

d = tempfile.mkdtemp()
# missing file -> defaults
c = load_controls(d)
assert c["paused"] is False and c["aggression"] is None and c["flatten_seq"] == 0
assert c["block"] == [] and c["allow"] is None
assert load_ack(d) == {"flatten_seq_done": 0, "apply_seq_done": 0, "refresh_seq_done": 0}

# atomic write round-trips and partial files merge over defaults
atomic_write_json(os.path.join(d, "controls.json"), {"paused": True, "aggression": 0.7})
c = load_controls(d)
assert c["paused"] is True and c["aggression"] == 0.7 and c["flatten_seq"] == 0  # missing keys defaulted
assert os.path.exists(os.path.join(d, "controls.json")) and not os.path.exists(os.path.join(d, "controls.json.tmp"))

# corrupt file -> defaults, no crash
open(os.path.join(d, "controls.json"), "w").write("{not json")
assert load_controls(d)["paused"] is False

# derisk_clamp mirrors _derisk_only: forbid open/increase/flip, allow reduction
assert derisk_clamp(5.0, 0.0) == 0.0      # cur flat -> don't open
assert derisk_clamp(-3.0, 2.0) == 0.0     # flip -> close to flat
assert derisk_clamp(0.0, 2.0) == 0.0      # to-flat -> close
assert derisk_clamp(5.0, 2.0) == 2.0      # don't increase -> hold cur
assert derisk_clamp(1.0, 2.0) == 1.0      # reduction allowed

# blocked_set: block list, plus everything not in allow when allow is set
syms = ["AUDUSD", "EURUSD", "USDJPY"]
assert blocked_set({"block": ["EURUSD"], "allow": None}, syms) == {"EURUSD"}
assert blocked_set({"block": [], "allow": ["AUDUSD"]}, syms) == {"EURUSD", "USDJPY"}
assert blocked_set({"block": [], "allow": None}, syms) == set()

# effective_aggression: null -> default, else the override
assert effective_aggression({"aggression": None}, 0.30) == 0.30
assert effective_aggression({"aggression": 0.9}, 0.30) == 0.9

# presets shipped
assert set(DEFAULT_PRESETS) == {"Default", "Survival", "Finals"}
assert DEFAULT_PRESETS["Default"]["aggression"] == 0.30
assert DEFAULT_PRESETS["Survival"]["aggression"] == 0.0
assert DEFAULT_PRESETS["Finals"]["aggression"] == 1.0
print("OK")
