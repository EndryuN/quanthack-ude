"""Pure per-symbol override accessors — no MT5, no engine."""
import json, os, tempfile
from bot.overrides import load_overrides, pinned_set, human_views, take_profits

d = tempfile.mkdtemp()
assert load_overrides(d) == {}, "missing file -> {}"

ov = {
    "EURUSD": {"pinned": True, "view": None, "take_profit": None, "note": "pinned only"},
    "USDJPY": {"pinned": False, "view": {"direction": "long", "confidence": 0.8},
               "take_profit": 153.5, "note": "yen proxy"},
    "XAUUSD": {"pinned": False, "view": None, "take_profit": None, "note": ""},
}
with open(os.path.join(d, "overrides.json"), "w", encoding="utf-8") as f:
    json.dump(ov, f)

o = load_overrides(d)
assert o == ov
assert pinned_set(o) == {"EURUSD"}
assert human_views(o) == {"USDJPY": {"direction": "long", "confidence": 0.8}}
assert take_profits(o) == {"USDJPY": 153.5}

# corrupt file -> {}
with open(os.path.join(d, "overrides.json"), "w") as f:
    f.write("{not json")
assert load_overrides(d) == {}
print("OK")
