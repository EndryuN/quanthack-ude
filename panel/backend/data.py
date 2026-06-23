"""Pure read-only loaders + transforms over the engine's decision logs and
equity samples. No MT5, no network — every function takes data in and returns
plain dicts/lists. The FastAPI layer (app.py) is a thin wrapper over these."""
from __future__ import annotations

import glob
import json
import os
from datetime import datetime, timezone


def load_decisions(log_dir: str, day: str | None = None) -> list[dict]:
    """All decision records across days (or one YYYY-MM-DD), in file order.
    Includes refresh_failed rows. Missing dir/files -> []."""
    if day:
        paths = [os.path.join(log_dir, f"ai_decisions_{day}.jsonl")]
    else:
        paths = sorted(glob.glob(os.path.join(log_dir, "ai_decisions_*.jsonl")))
    out: list[dict] = []
    for p in paths:
        if not os.path.exists(p):
            continue
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
    return out


def latest_decision(records: list[dict]) -> dict | None:
    """The most recent record that is a real decision (not refresh_failed)."""
    for r in reversed(records):
        if r.get("event") != "refresh_failed":
            return r
    return None


def equity_series(state_dir: str) -> list[dict]:
    """[{ts, equity}] from state/equity_samples.json. Missing file -> []."""
    path = os.path.join(state_dir, "equity_samples.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        obj = json.load(fh)
    out = []
    for item in obj.get("samples", []):
        epoch, eq = item[0], item[1]
        ts = datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        out.append({"ts": ts, "equity": float(eq)})
    return out
