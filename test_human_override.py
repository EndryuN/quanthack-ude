"""Human override flips/replaces a symbol's final view (the deciding voice)."""
from bot.second_opinion import apply_consensus, apply_human_override

# start from a real consensus result: Claude short AUDUSD
cv = {"AUDUSD": (-1, 0.45)}
nv = {"AUDUSD": (-1, 0.6, "agree")}
sizer, recs = apply_consensus(cv, {"AUDUSD": "x"}, nv, gain=0.5)
assert sizer["AUDUSD"][0] == -1   # short before override

# human overrides AUDUSD to LONG @0.9
sizer, recs = apply_human_override(sizer, recs, {"AUDUSD": {"direction": "long", "confidence": 0.9}})
assert sizer["AUDUSD"] == (1, 0.9), "human flipped to long 0.9"
r = next(r for r in recs if r["symbol"] == "AUDUSD")
assert r["direction"] == "long" and r["confidence"] == 0.9
assert r["human"] == {"direction": "long", "confidence": 0.9}
assert r["agreement"] == "human_override" and r["boosted"] is False

# a flat human override -> direction flat, confidence clamped
sizer2, recs2 = apply_consensus({"EURUSD": (1, 0.5)}, {"EURUSD": "y"}, {}, gain=0.5)
sizer2, recs2 = apply_human_override(sizer2, recs2, {"EURUSD": {"direction": "flat", "confidence": 2.0}})
assert sizer2["EURUSD"] == (0, 1.0)   # clamped to 1.0
assert recs2[0]["agreement"] == "human_override"

# no override for a symbol -> untouched
sizer3, recs3 = apply_consensus({"GBPUSD": (-1, 0.4)}, {"GBPUSD": "z"}, {}, gain=0.5)
sizer3, recs3 = apply_human_override(sizer3, recs3, {})
assert sizer3["GBPUSD"] == (-1, 0.4) and recs3[0]["agreement"] == "second_opinion_unavailable"
print("OK")
