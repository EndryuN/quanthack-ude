"""Presets: seed defaults, apply into controls, save current as a preset."""
import json, os, tempfile
from fastapi.testclient import TestClient
from panel.backend import app as appmod

d = tempfile.mkdtemp()
appmod.LOG_DIR = os.path.join("panel", "backend", "_fixtures")
appmod.STATE_DIR = d
client = TestClient(appmod.app)

# seeds the three default presets
p = client.get("/api/presets").json()
assert set(p["presets"]) == {"Default", "Survival", "Finals"}
assert p["presets"]["Survival"]["aggression"] == 0.0

# applying Finals writes its knobs into controls + bumps apply_seq + sets active
c = client.post("/api/presets/apply", json={"name": "Finals"}).json()
assert c["aggression"] == 1.0 and c["active_preset"] == "Finals" and c["apply_seq"] >= 1
assert json.load(open(os.path.join(d, "controls.json")))["aggression"] == 1.0

# set a custom knob, then save it as "MyMix"
client.post("/api/controls", json={"aggression": 0.45, "block": ["BTCUSD"]})
client.post("/api/presets/save", json={"name": "MyMix"})
p = client.get("/api/presets").json()
assert p["presets"]["MyMix"]["aggression"] == 0.45 and p["presets"]["MyMix"]["block"] == ["BTCUSD"]
print("OK")
