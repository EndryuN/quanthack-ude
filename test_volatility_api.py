"""Volatility endpoint (informational, panel-only). PANEL_NO_LIVE -> /api/volatility
degrades to available:false (no MT5) without crashing."""
import os
os.environ["PANEL_NO_LIVE"] = "1"
from fastapi.testclient import TestClient
from panel.backend import app as appmod

client = TestClient(appmod.app)

r = client.get("/api/volatility")
assert r.status_code == 200
j = r.json()
assert j["available"] is False and "note" in j     # no MT5 -> graceful, not a 500
print("OK /api/volatility degrades offline:", j["note"])
print("OK")
