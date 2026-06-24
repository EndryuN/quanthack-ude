"""Panel overrides API — per-symbol merge, app sole writer."""
import json, os, tempfile
from fastapi.testclient import TestClient
from panel.backend import app as appmod

d = tempfile.mkdtemp()
appmod.LOG_DIR = os.path.join("panel", "backend", "_fixtures")
appmod.STATE_DIR = d
client = TestClient(appmod.app)

assert client.get("/api/overrides").json() == {}, "empty by default"

# pin EURUSD
r = client.post("/api/overrides/EURUSD", json={"pinned": True})
assert r.status_code == 200 and r.json()["EURUSD"]["pinned"] is True
# add a view + tp to USDJPY; EURUSD pin preserved
client.post("/api/overrides/USDJPY", json={"view": {"direction": "long", "confidence": 0.7},
                                           "take_profit": 153.5, "note": "yen"})
o = client.get("/api/overrides").json()
assert o["EURUSD"]["pinned"] is True
assert o["USDJPY"]["view"]["direction"] == "long" and o["USDJPY"]["take_profit"] == 153.5
# partial update merges (unpin EURUSD without dropping other keys)
client.post("/api/overrides/USDJPY", json={"pinned": True})
o = client.get("/api/overrides").json()
assert o["USDJPY"]["pinned"] is True and o["USDJPY"]["take_profit"] == 153.5  # tp preserved
# persisted on disk
on_disk = json.load(open(os.path.join(d, "overrides.json")))
assert on_disk["EURUSD"]["pinned"] is True
print("OK")
