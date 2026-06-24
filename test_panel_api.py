"""API smoke via FastAPI TestClient against the fixture log dir. No live MT5
needed — MT5 routes must degrade, not crash."""
import os
os.environ["PANEL_NO_LIVE"] = "1"   # deterministic: don't overlay live MT5 over the fixture account
from fastapi.testclient import TestClient
from panel.backend import app as appmod

# point the app at the fixture dir + a missing state dir
appmod.LOG_DIR = os.path.join("panel", "backend", "_fixtures")
appmod.STATE_DIR = os.path.join("panel", "backend", "_fixtures")  # no equity file -> []
client = TestClient(appmod.app)

r = client.get("/api/state")
assert r.status_code == 200
j = r.json()
assert "account" in j and "gauges" in j and "consensus" in j and "regime" in j
assert j["account"]["gross_leverage"] == 3.0  # latest (19:16) record

r = client.get("/api/heatmap")
assert r.status_code == 200 and "symbols" in r.json()

r = client.get("/api/equity")
assert r.status_code == 200 and r.json()["series"] == []  # no equity file

r = client.get("/api/positions")
assert r.status_code == 200
assert r.json().get("available") in (True, False)  # degrades gracefully

r = client.get("/api/bars/EURUSD")
assert r.status_code == 200 and "available" in r.json()

r = client.get("/")
assert r.status_code == 200 and "text/html" in r.headers["content-type"]
print("OK")
