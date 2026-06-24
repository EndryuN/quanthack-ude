"""Panel control-write API. The app is the sole writer of controls.json."""
import json, os, tempfile
from fastapi.testclient import TestClient
from panel.backend import app as appmod

d = tempfile.mkdtemp()
appmod.LOG_DIR = os.path.join("panel", "backend", "_fixtures")
appmod.STATE_DIR = d
client = TestClient(appmod.app)

# defaults when no file yet
r = client.get("/api/controls"); assert r.status_code == 200
c = r.json(); assert c["paused"] is False and c["aggression"] is None and c["flatten_seq"] == 0

# POST merges knobs and bumps apply_seq, writes the file
r = client.post("/api/controls", json={"paused": True, "aggression": 0.5, "block": ["XAGUSD"]})
assert r.status_code == 200
c = r.json(); assert c["paused"] is True and c["aggression"] == 0.5 and c["block"] == ["XAGUSD"]
assert c["apply_seq"] == 1, "knob change bumped apply_seq"
on_disk = json.load(open(os.path.join(d, "controls.json")))
assert on_disk["paused"] is True and on_disk["apply_seq"] == 1

# another POST bumps apply_seq again and preserves prior knobs not sent
r = client.post("/api/controls", json={"aggression": 0.2})
c = r.json(); assert c["apply_seq"] == 2 and c["paused"] is True and c["aggression"] == 0.2

# flatten bumps flatten_seq (and not apply_seq)
r = client.post("/api/flatten"); assert r.status_code == 200
c = r.json(); assert c["flatten_seq"] == 1 and c["apply_seq"] == 2
print("OK")
