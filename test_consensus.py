"""Consensus: Claude sets direction; Nemotron only boosts on agreement."""
from bot.second_opinion import apply_consensus

cr = {"AUDUSD": "a", "USDJPY": "b", "EURUSD": "c", "GBPUSD": "d"}

# agree -> boost: 0.45 + (1-0.45)*0.5*0.60 = 0.615
sv, recs = apply_consensus({"AUDUSD": (-1, 0.45)}, cr,
                           {"AUDUSD": (-1, 0.60, "agree")}, gain=0.5)
assert abs(sv["AUDUSD"][1] - 0.615) < 1e-9 and sv["AUDUSD"][0] == -1
assert recs[0]["agreement"] == "agree" and recs[0]["boosted"] is True
assert recs[0]["nemotron"]["direction"] == "short"

# disagree -> unchanged
sv, recs = apply_consensus({"EURUSD": (-1, 0.42)}, cr,
                           {"EURUSD": (1, 0.30, "x")}, gain=0.5)
assert sv["EURUSD"] == (-1, 0.42)
assert recs[0]["agreement"] == "disagree" and recs[0]["boosted"] is False

# nemotron flat -> unchanged
sv, recs = apply_consensus({"USDJPY": (1, 0.40)}, cr,
                           {"USDJPY": (0, 0.0, "x")}, gain=0.5)
assert sv["USDJPY"] == (1, 0.40) and recs[0]["agreement"] == "nemotron_flat"

# second opinion unavailable -> unchanged, nemotron None
sv, recs = apply_consensus({"GBPUSD": (-1, 0.38)}, cr, {}, gain=0.5)
assert sv["GBPUSD"] == (-1, 0.38)
assert recs[0]["agreement"] == "second_opinion_unavailable"
assert recs[0]["nemotron"] is None

# boost stays bounded at 1.0
sv, _ = apply_consensus({"AUDUSD": (1, 0.95)}, cr,
                        {"AUDUSD": (1, 1.0, "x")}, gain=0.5)
assert sv["AUDUSD"][1] <= 1.0

print("OK")
