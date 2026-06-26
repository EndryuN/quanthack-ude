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

# --- extra gateway voters (DeepSeek), additive, INFLUENCE OFF by default ---
EV = {"deepseek": {"AUDUSD": (-1, 0.80, "d")}}
sv, recs = apply_consensus({"AUDUSD": (-1, 0.45)}, cr,
                           {"AUDUSD": (-1, 0.60, "n")}, gain=0.5, extra_voters=EV)
# combined = full consensus (nemotron+deepseek agree): mean(0.6,0.8)=0.7 -> 0.6425
assert abs(recs[0]["combined"]["confidence"] - 0.6425) < 1e-3, recs[0]["combined"]
# used = only influence-on voters: nemotron on (default), deepseek OFF -> 0.615
assert abs(sv["AUDUSD"][1] - 0.615) < 1e-3, sv["AUDUSD"]
assert recs[0]["voters"]["deepseek"]["agrees"] is True
assert recs[0]["voters"]["deepseek"]["influence"] is False
assert recs[0]["voters"]["nemotron"]["agrees"] is True

# extra_influence ON -> DeepSeek now also moves the USED value
sv, _ = apply_consensus({"AUDUSD": (-1, 0.45)}, cr,
                        {"AUDUSD": (-1, 0.60, "n")}, gain=0.5,
                        extra_voters=EV, extra_influence=True)
assert abs(sv["AUDUSD"][1] - 0.6425) < 1e-3, sv["AUDUSD"]

# a disagreeing DeepSeek NEVER shrinks and NEVER flips (additive only)
sv, recs = apply_consensus({"EURUSD": (1, 0.50)}, cr,
                           {"EURUSD": (0, 0.0, "n")}, gain=0.5, extra_influence=True,
                           extra_voters={"deepseek": {"EURUSD": (-1, 0.9, "d")}})
assert sv["EURUSD"] == (1, 0.50), sv["EURUSD"]            # unchanged, direction kept
assert recs[0]["voters"]["deepseek"]["agrees"] is False

print("OK")
