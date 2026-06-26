"""Human input has two modes:
 - 'override' (deciding): forces the direction + confidence (can flip the AI).
 - 'additive' (a second opinion): boosts confidence ONLY when it agrees with the
   current direction (cc + (1-cc)*gain*c); never flips or vetoes on disagree."""
from bot.second_opinion import apply_human_input, apply_human_override

# --- apply_human_input (sizing-time overlay) ---
v = {"EURUSD": (-1, 0.4)}
apply_human_input(v, {"EURUSD": {"direction": "long", "confidence": 0.7, "mode": "override"}}, gain=0.5)
assert v["EURUSD"] == (1, 0.7), v
print("OK override forces (flips short->long @ 0.7)")

v = {"EURUSD": (1, 0.4)}
apply_human_input(v, {"EURUSD": {"direction": "long", "confidence": 0.8, "mode": "additive"}}, gain=0.5)
exp = 0.4 + (1 - 0.4) * 0.5 * 0.8                       # 0.64
assert v["EURUSD"][0] == 1 and abs(v["EURUSD"][1] - exp) < 1e-9, v
print("OK additive+agree boosts confidence (0.40 -> 0.64)")

v = {"EURUSD": (1, 0.4)}
apply_human_input(v, {"EURUSD": {"direction": "short", "confidence": 0.9, "mode": "additive"}}, gain=0.5)
assert v["EURUSD"] == (1, 0.4), v                       # never flips/vetoes
print("OK additive+disagree is a no-op (no flip, no veto)")

v = {"EURUSD": (0, 0.0)}
apply_human_input(v, {"EURUSD": {"direction": "long", "confidence": 0.9, "mode": "additive"}}, gain=0.5)
assert v["EURUSD"] == (0, 0.0), v
print("OK additive vs flat AI is a no-op")

v = {"EURUSD": (-1, 0.4)}
apply_human_input(v, {"EURUSD": {"direction": "long", "confidence": 0.6}}, gain=0.5)
assert v["EURUSD"] == (1, 0.6), v                       # missing mode defaults to override
print("OK default mode = override")

v = {}
apply_human_input(v, {"BTCUSD": {"direction": "long", "confidence": 0.9, "mode": "override"}}, gain=0.5)
assert v == {}
print("OK symbol absent from views is ignored")

# --- apply_human_override (refresh-time): forces override, SKIPS additive ---
sv = {"EURUSD": (1, 0.4)}; recs = [{"symbol": "EURUSD", "direction": "long", "confidence": 0.4}]
apply_human_override(sv, recs, {"EURUSD": {"direction": "short", "confidence": 0.9, "mode": "additive"}})
assert sv["EURUSD"] == (1, 0.4), "additive must NOT be forced by apply_human_override"
apply_human_override(sv, recs, {"EURUSD": {"direction": "short", "confidence": 0.9, "mode": "override"}})
assert sv["EURUSD"] == (-1, 0.9) and recs[0]["agreement"] == "human_override"
print("OK apply_human_override skips additive, forces override")

print("OK")
