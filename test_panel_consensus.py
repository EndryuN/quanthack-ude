"""Consensus snapshot rows + the run-over-time heatmap matrix."""
import os
from panel.backend.data import load_decisions, latest_decision, consensus_rows, heatmap_matrix

recs = load_decisions(os.path.join("panel", "backend", "_fixtures"))
last = latest_decision(recs)  # the 19:16 record with one AUDUSD short

rows = consensus_rows(last)
assert all(r["direction"] != "flat" for r in rows), "flats excluded"
assert rows[0]["symbol"] == "AUDUSD"
assert rows[0]["nemotron"] is None and rows[0]["agreement"] == "second_opinion_unavailable"

# use the 18:31 record (richer) for sort + boost
rec0 = [r for r in recs if r.get("ts") == "2026-06-23T18:31:00"][0]
rows0 = consensus_rows(rec0)
assert rows0[0]["symbol"] == "AUDUSD" and rows0[0]["boosted"] is True
assert [r["symbol"] for r in rows0] == sorted(
    [r["symbol"] for r in rows0], key=lambda s: -{ "AUDUSD":0.62,"USDJPY":0.4,"EURUSD":0.42}[s]), "sorted by confidence desc"

hm = heatmap_matrix(recs)
assert "AUDUSD" in hm["symbols"]
# 3 normal records -> 3 timestamp columns (failed row excluded)
assert len(hm["timestamps"]) == 3
ai = hm["symbols"].index("AUDUSD")
assert all(hm["cells"][ai][c] is not None for c in range(3)), "AUDUSD present in all 3"
ei = hm["symbols"].index("EURCHF")
# EURCHF only flat in record 0 -> flat cell there, None elsewhere
assert hm["cells"][ei][0] is not None and hm["cells"][ei][0]["direction"] == "flat"
print("OK")
