"""Pure loaders over the fixture JSONL + a synthetic equity file."""
import json
import os
import tempfile

from panel.backend.data import load_decisions, latest_decision, equity_series

FIX = os.path.join("panel", "backend", "_fixtures")

recs = load_decisions(FIX)
assert len(recs) == 4, f"loaded all 4 fixture rows, got {len(recs)}"
assert any(r.get("event") == "refresh_failed" for r in recs)

last = latest_decision(recs)
assert last is not None and "event" not in last
assert last["ts"] == "2026-06-23T19:16:00", "latest skips the failed row and is the last normal record"

# equity series from a synthetic state dir
d = tempfile.mkdtemp()
json.dump({"samples": [[1750700000, 1000000.0], [1750700900, 1019620.0]],
           "peak": 1019620.0, "max_dd": 0.0},
          open(os.path.join(d, "equity_samples.json"), "w"))
es = equity_series(d)
assert len(es) == 2 and es[1]["equity"] == 1019620.0
assert es[0]["ts"].startswith("20"), "epoch converted to ISO timestamp"

# missing equity file -> empty list, no crash
assert equity_series(tempfile.mkdtemp()) == []
print("OK")
