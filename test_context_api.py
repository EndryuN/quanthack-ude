"""Context Control endpoint + line parser. PANEL_NO_LIVE -> /api/context degrades
to the SYSTEM prompt + template + field reference (never blank), and the parser
extracts the per-instrument fields from a build_context line."""
import os
os.environ["PANEL_NO_LIVE"] = "1"          # no MT5 -> offline/degraded path
from fastapi.testclient import TestClient
from panel.backend import app as appmod

client = TestClient(appmod.app)

# --- offline /api/context: still serves the prompt, template, fields ---
r = client.get("/api/context")
assert r.status_code == 200
j = r.json()
assert j["available"] is False                      # no MT5 -> no live snapshot
assert j["system"] and "signal engine" in j["system"]
assert "{CONTEXT}" in j["user_template"] and "{N}" in j["user_template"]
assert j["model"] == "claude-opus-4-8"
assert j["field_reference"] and any(f["field"] == "mom_z" for f in j["field_reference"])
assert j["not_fed"] and j["example_line"].startswith("EURUSD:")
assert isinstance(j["symbols"], list) and "BARUSD" not in j["symbols"]
print("OK offline /api/context degrades with prompt + template + fields")

# --- the line parser pulls fields out of a real build_context line ---
line = ("EURUSD: px=1.08423 | ret 1h=-0.04 4h=+0.11 1d=-0.23 3d=+0.87% | "
        "trend M15=up H1=up H4=dn | mom_z=+0.41 vol/bar=0.06% | 5d-range=61%")
row = appmod._parse_ctx_line(line)
assert row["symbol"] == "EURUSD" and row["line"] == line
assert row["px"] == "1.08423"
assert row["ret"]["1h"] == "-0.04" and row["ret"]["3d"] == "+0.87%"
assert row["trend"]["M15"] == "up" and row["trend"]["H4"] == "dn"
assert row["mom_z"] == "+0.41" and row["vol_bar"] == "0.06%" and row["range_5d"] == "61%"
print("OK _parse_ctx_line extracts all fields")

# malformed line -> still returns a symbol + the raw line, no crash
bad = appmod._parse_ctx_line("WEIRD no colon here")
assert "line" in bad and "symbol" in bad
print("OK parser tolerant of malformed lines")

print("OK")
