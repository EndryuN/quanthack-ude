"""The Nemotron toggle controls INFLUENCE only: Nemotron is always queried and
recorded; the toggle decides whether the algorithm acts on its input. Pure logic."""
from bot.controls import control_flag, DEFAULT_CONTROLS
from bot.second_opinion import apply_consensus

# default control value is None -> influence defaults ON
assert DEFAULT_CONTROLS["second_opinion"] is None
assert control_flag(None, "second_opinion", True) is True          # config default
assert control_flag({"second_opinion": None}, "second_opinion", True) is True
assert control_flag({"second_opinion": False}, "second_opinion", True) is False  # operator off
assert control_flag({"second_opinion": True}, "second_opinion", False) is True   # operator on

cr = {"AUDUSD": "a"}
agree_nemo = {"AUDUSD": (-1, 0.60, "agree")}

# influence ON: the sizer uses the boosted (combined) confidence
sv, recs = apply_consensus({"AUDUSD": (-1, 0.45)}, cr, agree_nemo, gain=0.5, influence=True)
assert abs(sv["AUDUSD"][1] - 0.615) < 1e-9                          # boosted value is used
assert recs[0]["combined"]["confidence"] == 0.615
assert recs[0]["nemotron"]["confidence"] == 0.60                    # still recorded
assert recs[0]["influence"] is True

# influence OFF: the sizer uses Claude's confidence, but Nemotron + combined are
# STILL recorded (so the panel can show Claude / Nemotron / Combined over time)
sv, recs = apply_consensus({"AUDUSD": (-1, 0.45)}, cr, agree_nemo, gain=0.5, influence=False)
assert sv["AUDUSD"][1] == 0.45                                      # algorithm ignored Nemotron
assert recs[0]["confidence"] == 0.45                               # used value = Claude's
assert recs[0]["combined"]["confidence"] == 0.615                  # combined still computed
assert recs[0]["nemotron"]["confidence"] == 0.60                   # nemotron still recorded
assert recs[0]["claude"]["confidence"] == 0.45
assert recs[0]["influence"] is False

print("OK")
