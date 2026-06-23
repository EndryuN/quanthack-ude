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


def _slim(side: dict | None) -> dict | None:
    if side is None:
        return None
    return {"direction": side["direction"], "confidence": side["confidence"]}


def consensus_rows(record: dict) -> list[dict]:
    """Per-view rows for the snapshot dumbbell, non-flat only, sorted by final
    confidence descending."""
    rows = []
    for v in record.get("views", []):
        if v["direction"] == "flat":
            continue
        rows.append({
            "symbol": v["symbol"],
            "direction": v["direction"],
            "confidence": v["confidence"],
            "claude": _slim(v.get("claude")),
            "nemotron": _slim(v.get("nemotron")),
            "agreement": v["agreement"],
            "boosted": v.get("boosted", False),
        })
    rows.sort(key=lambda r: -r["confidence"])
    return rows


def heatmap_matrix(records: list[dict]) -> dict:
    """instruments x timesteps grid. cells[r][c] is the view dict or None."""
    decisions = [r for r in records if r.get("event") != "refresh_failed"]
    symbols: list[str] = []
    for rec in decisions:
        for v in rec.get("views", []):
            if v["symbol"] not in symbols:
                symbols.append(v["symbol"])
    timestamps = [rec["ts"] for rec in decisions]
    cells = [[None] * len(decisions) for _ in symbols]
    sidx = {s: i for i, s in enumerate(symbols)}
    for c, rec in enumerate(decisions):
        for v in rec.get("views", []):
            cells[sidx[v["symbol"]]][c] = {
                "direction": v["direction"], "confidence": v["confidence"],
                "agreement": v["agreement"], "boosted": v.get("boosted", False),
            }
    return {"symbols": symbols, "timestamps": timestamps, "cells": cells}
