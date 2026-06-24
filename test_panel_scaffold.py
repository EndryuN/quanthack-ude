"""Panel package imports and the test fixture is present and parseable."""
import json
import os

import panel.backend  # noqa: F401  -- package importable

fixture = os.path.join("panel", "backend", "_fixtures", "ai_decisions_2026-06-23.jsonl")
assert os.path.exists(fixture), "fixture jsonl exists"
rows = [json.loads(l) for l in open(fixture, encoding="utf-8") if l.strip()]
assert len(rows) >= 3, "fixture has >=3 records"
normal = [r for r in rows if "event" not in r]
assert normal and "views" in normal[0] and "account" in normal[0]
assert any(r.get("event") == "refresh_failed" for r in rows), "fixture has a failed-refresh row"
import fastapi, uvicorn  # noqa: F401  -- deps importable
print("OK")
