# Control Panel — Phase 1 (Read-Only Dashboard) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A read-only web dashboard (the SIGNAL//LAYER UI) that visualizes the bot's decisions — Claude×Nemotron consensus, equity vs the risk floor, risk gauges, positions, and per-symbol price with per-model projection cones — without touching the trading engine at all.

**Architecture:** A new isolated `panel/` tree. A FastAPI backend reads the existing `logs/ai_decisions_*.jsonl` and `state/equity_samples.json`, plus live MT5 read-only methods, transforms them via a pure data layer, and serves a custom single-page frontend that renders charts with Plotly.js. Nothing in `bot/` is modified.

**Tech Stack:** Python 3.13, FastAPI + uvicorn (already installed), pandas, Plotly.js (CDN), vanilla JS/CSS. Tests: standalone `test_*.py` scripts.

## Global Constraints

- **DO NOT TOUCH `bot/` OR THE ENGINE.** Phase 1 is read-only. No file under `bot/` is created or modified. The live competition bot must keep running byte-for-byte as today. (Spec §1a.)
- **Isolated branch.** All work lands on branch `feat/control-panel`, never on the branch the operator runs.
- **No new dependency beyond `fastapi` + `uvicorn`** (both already installed; add them to `requirements.txt`). Plotly.js loads from CDN in the browser — no Python dep.
- **Tests:** standalone scripts named `test_*.py` at repo root, run with `python test_x.py`, using plain `assert` and printing `OK` on success. No pytest.
- **Degrade gracefully:** every endpoint and panel must work from JSONL alone when MT5 is unavailable (MT5 data is additive). MT5 is Windows-only and may be absent.
- **Decision-record schema (read-only consumer)** — each JSONL line: `ts, mode, models{primary,second_opinion}, regime{claude,nemotron}, account{equity,gross_leverage,margin_usage,margin_level,return_pct,drawdown,sharpe}, cost{claude,nemotron}, views[]{symbol,direction,confidence,claude{direction,confidence,rationale},nemotron{...}|null,agreement,boosted}, orders[]{symbol,side,lots,cur,target,sl}`. Failed refreshes: `{ts, event:"refresh_failed", error}`.
- **Risk thresholds for gauges** (import from `bot.config`, read-only): kill `margin_level<150`, warn `<250`; `max_gross_leverage=12`, target `6`; `max_margin_usage=0.60`; `daily_max_drawdown=0.20`. Tripwires (from `bot/risk.py`): leverage>25 (penalty 28), margin>0.85 (penalty 0.90), concentration>0.85 (penalty 0.90), each sustained ≥30 min.
- **Palette/type (SIGNAL//LAYER):** Canvas `#0D0E12`, Surface `#14161D`, Lift `#1E2330`, Chartreuse `#C8FF00`, Claude/violet `#7B61FF`, Nemotron/teal `#00C8A0`, Human/amber `#F5A623`, Long `#2ECC71`, Short `#FF4D4D`, Flat `#3A3F50`, Ink `#E8EAF0`, Muted `#6B7080`. Fonts: Space Grotesk (display), IBM Plex Sans (body), JetBrains Mono (data).

---

### Task 1: Scaffold the `panel/` tree, deps, launcher, and a test fixture

**Files:**
- Create: `panel/__init__.py`, `panel/backend/__init__.py`
- Create: `panel/backend/_fixtures/sample_decisions.jsonl`
- Create: `panel/run_panel.ps1`
- Modify: `requirements.txt` (add fastapi, uvicorn)
- Test: `test_panel_scaffold.py`

**Interfaces:**
- Produces: the `panel.backend` package (importable), and the fixture path `panel/backend/_fixtures/sample_decisions.jsonl` consumed by later tests.

- [ ] **Step 1: Write the failing test**

Create `test_panel_scaffold.py`:
```python
"""Panel package imports and the test fixture is present and parseable."""
import json
import os

import panel.backend  # noqa: F401  -- package importable

fixture = os.path.join("panel", "backend", "_fixtures", "sample_decisions.jsonl")
assert os.path.exists(fixture), "fixture jsonl exists"
rows = [json.loads(l) for l in open(fixture, encoding="utf-8") if l.strip()]
assert len(rows) >= 3, "fixture has >=3 records"
normal = [r for r in rows if "event" not in r]
assert normal and "views" in normal[0] and "account" in normal[0]
assert any(r.get("event") == "refresh_failed" for r in rows), "fixture has a failed-refresh row"
import fastapi, uvicorn  # noqa: F401  -- deps importable
print("OK")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python test_panel_scaffold.py`
Expected: `ModuleNotFoundError: No module named 'panel'`

- [ ] **Step 3: Create the package files**

Create empty `panel/__init__.py` and `panel/backend/__init__.py` (0 bytes each).

- [ ] **Step 4: Create the fixture**

Create `panel/backend/_fixtures/sample_decisions.jsonl` (each object on ONE line; shown wrapped for readability — write them single-line):
```jsonl
{"ts":"2026-06-23T18:31:00","mode":"dry-run","models":{"primary":"claude-opus-4-8","second_opinion":"nvidia/nemotron-3-super-120b-a12b"},"regime":{"claude":"Broad USD strength, risk-off.","nemotron":"USD bid; commodities heavy."},"account":{"equity":1019620,"gross_leverage":3.1,"margin_usage":0.11,"margin_level":937,"return_pct":0.0196,"drawdown":0.001,"sharpe":-0.19},"cost":{"claude":{"in":2494,"out":1270,"usd":0.044},"nemotron":{"in":2510,"out":880,"usd":0.0}},"views":[{"symbol":"AUDUSD","direction":"short","confidence":0.62,"claude":{"direction":"short","confidence":0.45,"rationale":"USD bid pressures commodity FX."},"nemotron":{"direction":"short","confidence":0.6,"rationale":"USD strength."},"agreement":"agree","boosted":true},{"symbol":"USDJPY","direction":"long","confidence":0.4,"claude":{"direction":"long","confidence":0.4,"rationale":"Yield-driven."},"nemotron":{"direction":"flat","confidence":0.0,"rationale":"No view."},"agreement":"nemotron_flat","boosted":false},{"symbol":"EURUSD","direction":"short","confidence":0.42,"claude":{"direction":"short","confidence":0.42,"rationale":"Soft euro."},"nemotron":{"direction":"long","confidence":0.3,"rationale":"Bounce."},"agreement":"disagree","boosted":false},{"symbol":"EURCHF","direction":"flat","confidence":0.0,"claude":{"direction":"flat","confidence":0.0,"rationale":"Range."},"nemotron":null,"agreement":"second_opinion_unavailable","boosted":false}],"orders":[{"symbol":"USDJPY","side":"buy","lots":1.56,"cur":2.86,"target":4.42,"sl":153.492}]}
{"ts":"2026-06-23T18:46:00","mode":"dry-run","models":{"primary":"claude-opus-4-8","second_opinion":"nvidia/nemotron-3-super-120b-a12b"},"regime":{"claude":"USD strength persists.","nemotron":"Risk-off."},"account":{"equity":1020150,"gross_leverage":3.2,"margin_usage":0.115,"margin_level":920,"return_pct":0.0202,"drawdown":0.001,"sharpe":-0.1},"cost":{"claude":{"in":2490,"out":1200,"usd":0.043},"nemotron":{"in":2500,"out":900,"usd":0.0}},"views":[{"symbol":"AUDUSD","direction":"short","confidence":0.66,"claude":{"direction":"short","confidence":0.5,"rationale":"Trend down."},"nemotron":{"direction":"short","confidence":0.55,"rationale":"USD up."},"agreement":"agree","boosted":true},{"symbol":"EURUSD","direction":"short","confidence":0.45,"claude":{"direction":"short","confidence":0.45,"rationale":"Weak."},"nemotron":{"direction":"short","confidence":0.4,"rationale":"Weak."},"agreement":"agree","boosted":false}],"orders":[]}
{"ts":"2026-06-23T19:01:00","event":"refresh_failed","error":"No module named 'anthropic'"}
{"ts":"2026-06-23T19:16:00","mode":"dry-run","models":{"primary":"claude-opus-4-8","second_opinion":null},"regime":{"claude":"Stabilizing.","nemotron":null},"account":{"equity":1019980,"gross_leverage":3.0,"margin_usage":0.108,"margin_level":945,"return_pct":0.02,"drawdown":0.0012,"sharpe":-0.05},"cost":{"claude":{"in":2480,"out":1100,"usd":0.041},"nemotron":null},"views":[{"symbol":"AUDUSD","direction":"short","confidence":0.5,"claude":{"direction":"short","confidence":0.5,"rationale":"Down."},"nemotron":null,"agreement":"second_opinion_unavailable","boosted":false}],"orders":[]}
```

- [ ] **Step 5: Add deps to requirements.txt**

Append to `requirements.txt`:
```
# Control panel dashboard (panel/) — read-only web UI
fastapi>=0.115.0
uvicorn>=0.30.0
```

- [ ] **Step 6: Create the launcher**

Create `panel/run_panel.ps1`:
```powershell
# Launch the read-only control panel dashboard on http://127.0.0.1:8800
# Reads logs/ + state/ + live MT5 (read-only). Does NOT touch the trading engine.
$ErrorActionPreference = "Stop"
Set-Location -Path (Join-Path $PSScriptRoot "..")
python -m uvicorn panel.backend.app:app --host 127.0.0.1 --port 8800
```

- [ ] **Step 7: Run test to verify it passes**

Run: `python test_panel_scaffold.py`
Expected: `OK`

- [ ] **Step 8: Commit**

```bash
git add panel/__init__.py panel/backend/__init__.py panel/backend/_fixtures/sample_decisions.jsonl panel/run_panel.ps1 requirements.txt test_panel_scaffold.py
git commit -m "feat(panel): scaffold read-only dashboard package + fixture"
```

---

### Task 2: Data loaders — decisions + equity series

**Files:**
- Create: `panel/backend/data.py`
- Test: `test_panel_data.py`

**Interfaces:**
- Produces: `load_decisions(log_dir: str, day: str | None = None) -> list[dict]` (globs `ai_decisions_*.jsonl`, or one day's file; returns all rows in file order including `refresh_failed` rows).
- Produces: `latest_decision(records: list[dict]) -> dict | None` (last row that is NOT a `refresh_failed` event, else None).
- Produces: `equity_series(state_dir: str) -> list[dict]` (reads `equity_samples.json`; returns `[{"ts": iso, "equity": float}, ...]`).

- [ ] **Step 1: Confirm the equity-samples shape**

Run: `git grep -n "equity_samples\|samples\|json.dump" bot/state.py`
Read the lines shown to confirm the JSON key holding the sample list and the per-sample shape (expected: a `"samples"` list of `[epoch_seconds, equity]` pairs). Use the confirmed key in Step 4. If the key differs, adjust `equity_series` accordingly and note it in your report.

- [ ] **Step 2: Write the failing test**

Create `test_panel_data.py`:
```python
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python test_panel_data.py`
Expected: `ModuleNotFoundError: No module named 'panel.backend.data'`

- [ ] **Step 4: Implement the loaders**

Create `panel/backend/data.py`:
```python
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
    obj = json.load(open(path, encoding="utf-8"))
    out = []
    for item in obj.get("samples", []):
        epoch, eq = item[0], item[1]
        ts = datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        out.append({"ts": ts, "equity": float(eq)})
    return out
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python test_panel_data.py`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add panel/backend/data.py test_panel_data.py
git commit -m "feat(panel): decision + equity loaders"
```

---

### Task 3: Transforms — consensus rows + heatmap matrix

**Files:**
- Modify: `panel/backend/data.py` (append functions)
- Test: `test_panel_consensus.py`

**Interfaces:**
- Consumes: a decision record (Task 1 schema).
- Produces: `consensus_rows(record: dict) -> list[dict]` — per non-flat view, sorted by final confidence desc: `{symbol, direction, confidence, claude:{direction,confidence}, nemotron:{direction,confidence}|None, agreement, boosted}`.
- Produces: `heatmap_matrix(records: list[dict]) -> dict` — `{symbols: [..], timestamps: [..], cells: [[{direction,confidence,agreement,boosted}|None]]}` (rows=symbols, cols=timesteps; None where a symbol absent at a timestep).

- [ ] **Step 1: Write the failing test**

Create `test_panel_consensus.py`:
```python
"""Consensus snapshot rows + the run-over-time heatmap matrix."""
import os
from panel.backend.data import load_decisions, latest_decision, consensus_rows, heatmap_matrix

recs = load_decisions(os.path.join("panel", "backend", "_fixtures"))
last = latest_decision(recs)  # the 19:16 record with one AUDUSD short

rows = consensus_rows(last)
assert all(r["direction"] != "flat" for r in rows), "flats excluded"
assert rows[0]["symbol"] == "AUDUSD"
assert rows[0]["nemotron"] is None and rows[0]["agreement"] == "second_opinion_unavailable"

# use the 18:31 record (richer) for sort + boost
rec0 = [r for r in recs if r.get("ts") == "2026-06-23T18:31:00"][0]
rows0 = consensus_rows(rec0)
assert rows0[0]["symbol"] == "AUDUSD" and rows0[0]["boosted"] is True
assert [r["symbol"] for r in rows0] == sorted(
    [r["symbol"] for r in rows0], key=lambda s: -{ "AUDUSD":0.62,"USDJPY":0.4,"EURUSD":0.42}[s]), "sorted by confidence desc"

hm = heatmap_matrix(recs)
assert "AUDUSD" in hm["symbols"]
# 3 normal records -> 3 timestamp columns (failed row excluded)
assert len(hm["timestamps"]) == 3
ai = hm["symbols"].index("AUDUSD")
assert all(hm["cells"][ai][c] is not None for c in range(3)), "AUDUSD present in all 3"
ei = hm["symbols"].index("EURCHF")
# EURCHF only flat in record 0 -> flat cell there, None elsewhere
assert hm["cells"][ei][0] is not None and hm["cells"][ei][0]["direction"] == "flat"
print("OK")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python test_panel_consensus.py`
Expected: `ImportError: cannot import name 'consensus_rows'`

- [ ] **Step 3: Implement the transforms**

Append to `panel/backend/data.py`:
```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python test_panel_consensus.py`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add panel/backend/data.py test_panel_consensus.py
git commit -m "feat(panel): consensus rows + heatmap matrix transforms"
```

---

### Task 4: Transforms — risk gauges + tripwires (uses RiskLimits, read-only)

**Files:**
- Modify: `panel/backend/data.py` (append)
- Test: `test_panel_gauges.py`

**Interfaces:**
- Consumes: a decision record's `account` block + `bot.config.RiskLimits` (imported read-only).
- Produces: `risk_gauges(account: dict) -> list[dict]` — one per gauge: `{key, label, value, unit, bands:[{to,color}], hard:[{at,label}]}` for margin_level, gross_leverage, margin_usage, drawdown.
- Produces: `tripwire_status(account: dict) -> list[dict]` — `{key, label, active: bool, value, warn, penalty}` for lev>25/28, margin>0.85/0.90 (concentration needs positions; omit here — added live in the API layer).

- [ ] **Step 1: Write the failing test**

Create `test_panel_gauges.py`:
```python
"""Risk gauges + tripwire flags derived from an account snapshot."""
from panel.backend.data import risk_gauges, tripwire_status

acct = {"equity": 1019620, "gross_leverage": 3.1, "margin_usage": 0.11,
        "margin_level": 937, "return_pct": 0.0196, "drawdown": 0.001, "sharpe": -0.19}

g = {x["key"]: x for x in risk_gauges(acct)}
assert set(g) == {"margin_level", "gross_leverage", "margin_usage", "drawdown"}
assert g["gross_leverage"]["value"] == 3.1
assert any(h["at"] == 12 for h in g["gross_leverage"]["hard"]), "12x cap marked"
assert any(h["at"] == 150 for h in g["margin_level"]["hard"]), "150% kill marked"
assert g["margin_usage"]["unit"] == "%" and abs(g["margin_usage"]["value"] - 11.0) < 1e-6

# safe state -> no tripwire active
tw = {x["key"]: x for x in tripwire_status(acct)}
assert tw["leverage"]["active"] is False and tw["margin"]["active"] is False

# breach state
hot = dict(acct, gross_leverage=26.0, margin_usage=0.88)
tw2 = {x["key"]: x for x in tripwire_status(hot)}
assert tw2["leverage"]["active"] is True and tw2["leverage"]["penalty"] == 28
assert tw2["margin"]["active"] is True
print("OK")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python test_panel_gauges.py`
Expected: `ImportError: cannot import name 'risk_gauges'`

- [ ] **Step 3: Implement the gauge transforms**

Append to `panel/backend/data.py`:
```python
from bot.config import RiskLimits  # read-only import of the limit constants

_LIM = RiskLimits()
_GREEN, _AMBER, _RED = "#2ECC71", "#F5A623", "#FF4D4D"


def risk_gauges(account: dict) -> list[dict]:
    """Gauge specs (value + colored bands + hard threshold marks) for the four
    risk dials. Thresholds come from bot.config.RiskLimits (read-only)."""
    ml = account["margin_level"]
    lev = account["gross_leverage"]
    mu = account["margin_usage"] * 100.0
    dd = account["drawdown"] * 100.0
    return [
        {"key": "margin_level", "label": "Margin Level", "value": ml, "unit": "%",
         "max": 1000,
         "bands": [{"to": _LIM.kill_switch_margin_level, "color": _RED},
                   {"to": _LIM.warn_margin_level, "color": _AMBER},
                   {"to": 1000, "color": _GREEN}],
         "hard": [{"at": _LIM.kill_switch_margin_level, "label": "KILL"},
                  {"at": _LIM.warn_margin_level, "label": "no-new-risk"}]},
        {"key": "gross_leverage", "label": "Gross Leverage", "value": lev, "unit": "x",
         "max": 30,
         "bands": [{"to": _LIM.target_gross_leverage, "color": _GREEN},
                   {"to": _LIM.max_gross_leverage, "color": _AMBER},
                   {"to": 30, "color": _RED}],
         "hard": [{"at": _LIM.max_gross_leverage, "label": "cap"}, {"at": 28, "label": "penalty"}]},
        {"key": "margin_usage", "label": "Margin Usage", "value": mu, "unit": "%",
         "max": 100,
         "bands": [{"to": _LIM.max_margin_usage * 100 * 0.83, "color": _GREEN},
                   {"to": _LIM.max_margin_usage * 100, "color": _AMBER},
                   {"to": 100, "color": _RED}],
         "hard": [{"at": _LIM.max_margin_usage * 100, "label": "cap"}, {"at": 90, "label": "penalty"}]},
        {"key": "drawdown", "label": "Drawdown", "value": dd, "unit": "%",
         "max": 30,
         "bands": [{"to": 10, "color": _GREEN},
                   {"to": _LIM.daily_max_drawdown * 100, "color": _AMBER},
                   {"to": 30, "color": _RED}],
         "hard": [{"at": _LIM.daily_max_drawdown * 100, "label": "KILL"}]},
    ]


def tripwire_status(account: dict) -> list[dict]:
    """Competition tripwire flags from the account snapshot (concentration is
    added by the API layer when live positions are available)."""
    lev = account["gross_leverage"]
    mu = account["margin_usage"]
    return [
        {"key": "leverage", "label": "Leverage > 25x", "value": lev,
         "active": lev > 25, "warn": 25, "penalty": 28},
        {"key": "margin", "label": "Margin > 85%", "value": mu,
         "active": mu > 0.85, "warn": 0.85, "penalty": 0.90},
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python test_panel_gauges.py`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add panel/backend/data.py test_panel_gauges.py
git commit -m "feat(panel): risk gauge + tripwire transforms"
```

---

### Task 5: Projection-cone math (pure)

**Files:**
- Create: `panel/backend/projection.py`
- Test: `test_panel_projection.py`

**Interfaces:**
- Produces: `projection_cone(price: float, direction: str, confidence: float, vol: float, horizon_bars: int = 8) -> dict` → `{"mid": [[i, price_i], ...], "upper": [...], "lower": [...]}` where the mid line slopes per direction×confidence and the band half-width grows with `vol` and `sqrt(step)`. `flat` or `confidence==0` → a flat mid line at `price` with a vol band.

- [ ] **Step 1: Write the failing test**

Create `test_panel_projection.py`:
```python
"""Forward projection cone: slope by direction*confidence, width by vol."""
from panel.backend.projection import projection_cone

p = 100.0
up = projection_cone(p, "long", 0.5, vol=0.01, horizon_bars=8)
assert up["mid"][0] == [0, 100.0], "starts at current price"
assert up["mid"][-1][1] > p, "long slopes up"
assert up["upper"][-1][1] > up["mid"][-1][1] > up["lower"][-1][1], "band brackets mid"

dn = projection_cone(p, "short", 0.5, vol=0.01)
assert dn["mid"][-1][1] < p, "short slopes down"

flat = projection_cone(p, "flat", 0.0, vol=0.01)
assert flat["mid"][-1][1] == p, "flat stays at price"
assert flat["upper"][-1][1] > p, "flat still has a vol band"

# higher confidence -> steeper slope
strong = projection_cone(p, "long", 0.9, vol=0.01)
assert strong["mid"][-1][1] > up["mid"][-1][1]
# higher vol -> wider band
wide = projection_cone(p, "long", 0.5, vol=0.05)
assert (wide["upper"][-1][1] - wide["lower"][-1][1]) > (up["upper"][-1][1] - up["lower"][-1][1])
print("OK")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python test_panel_projection.py`
Expected: `ModuleNotFoundError: No module named 'panel.backend.projection'`

- [ ] **Step 3: Implement the projection**

Create `panel/backend/projection.py`:
```python
"""Pure forward-projection geometry for the per-symbol chart. The models output
direction + confidence (NOT price targets), so this draws an honest directional
cone: the mid line slopes with direction*confidence; the cone half-width grows
with the instrument's per-bar volatility and sqrt(time)."""
from __future__ import annotations

import math

_DIR = {"long": 1.0, "short": -1.0, "flat": 0.0}

# how far a confidence=1 view projects the mid over `horizon_bars`, as a
# fraction of price-per-unit-vol. Tuned so a strong call leans visibly but
# stays within a few vol units (no fabricated magnitude claims).
_SLOPE_VOL_UNITS = 6.0


def projection_cone(price: float, direction: str, confidence: float,
                    vol: float, horizon_bars: int = 8) -> dict:
    """Returns {mid, upper, lower}, each a list of [step, price]."""
    d = _DIR.get(direction, 0.0)
    vol = max(vol, 1e-6)
    # total mid drift over the horizon, in price terms
    drift = d * confidence * _SLOPE_VOL_UNITS * vol * price
    mid, upper, lower = [], [], []
    for i in range(horizon_bars + 1):
        frac = i / horizon_bars
        m = price + drift * frac
        half = price * vol * math.sqrt(i)   # band widens with sqrt(time)
        mid.append([i, round(m, 8)])
        upper.append([i, round(m + half, 8)])
        lower.append([i, round(m - half, 8)])
    return {"mid": mid, "upper": upper, "lower": lower}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python test_panel_projection.py`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add panel/backend/projection.py test_panel_projection.py
git commit -m "feat(panel): forward projection-cone geometry"
```

---

### Task 6: FastAPI backend — endpoints + static serving + optional live MT5

**Files:**
- Create: `panel/backend/app.py`
- Create: `panel/frontend/index.html` (minimal placeholder so static mount has a file; real UI in Tasks 7-9)
- Test: `test_panel_api.py`

**Interfaces:**
- Consumes: all `panel.backend.data` + `panel.backend.projection` functions; `bot.client.MT5Client` (read-only, optional).
- Produces (HTTP): `GET /api/state`, `GET /api/decisions`, `GET /api/heatmap`, `GET /api/equity`, `GET /api/positions`, `GET /api/bars/{symbol}`, and `GET /` (serves the frontend). Each returns JSON; MT5-backed routes degrade to `{"available": false}` when MT5 is absent.

- [ ] **Step 1: Write the failing test**

Create `test_panel_api.py`:
```python
"""API smoke via FastAPI TestClient against the fixture log dir. No live MT5
needed — MT5 routes must degrade, not crash."""
import os
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python test_panel_api.py`
Expected: `ModuleNotFoundError: No module named 'panel.backend.app'`

- [ ] **Step 3: Create the placeholder frontend file**

Create `panel/frontend/index.html`:
```html
<!doctype html><html><head><meta charset="utf-8"><title>SIGNAL//LAYER</title></head>
<body>panel placeholder</body></html>
```

- [ ] **Step 4: Implement the app**

Create `panel/backend/app.py`:
```python
"""Read-only dashboard backend. Reads the engine's logs/state files and (when
available) live MT5 via the existing read-only MT5Client. NEVER writes engine
state and NEVER imports anything that mutates trading. The trading engine is
untouched by this process."""
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import data, projection

# overridable in tests; default to the repo's live dirs
LOG_DIR = os.environ.get("PANEL_LOG_DIR", "logs")
STATE_DIR = os.environ.get("PANEL_STATE_DIR", "state")

_HERE = os.path.dirname(os.path.abspath(__file__))
FRONTEND = os.path.join(_HERE, "..", "frontend")

app = FastAPI(title="SIGNAL//LAYER")


def _mt5():
    """Return a connected read-only MT5Client, or None if unavailable.
    Read-only: we only call account()/positions()/bars()."""
    try:
        from bot.client import MT5Client
        c = MT5Client()
        c.connect()
        return c
    except Exception:
        return None


@app.get("/api/state")
def state():
    recs = data.load_decisions(LOG_DIR)
    last = data.latest_decision(recs)
    if last is None:
        return {"account": None, "gauges": [], "tripwires": [], "consensus": [],
                "regime": {}, "models": {}, "cost": {}, "ts": None}
    acct = last["account"]
    return {
        "ts": last["ts"], "mode": last.get("mode"),
        "account": acct,
        "gauges": data.risk_gauges(acct),
        "tripwires": data.tripwire_status(acct),
        "consensus": data.consensus_rows(last),
        "regime": last.get("regime", {}),
        "models": last.get("models", {}),
        "cost": last.get("cost", {}),
        "orders": last.get("orders", []),
    }


@app.get("/api/decisions")
def decisions():
    return {"records": data.load_decisions(LOG_DIR)}


@app.get("/api/heatmap")
def heatmap():
    return data.heatmap_matrix(data.load_decisions(LOG_DIR))


@app.get("/api/equity")
def equity():
    return {"series": data.equity_series(STATE_DIR)}


@app.get("/api/positions")
def positions():
    c = _mt5()
    if c is None:
        return {"available": False, "positions": []}
    try:
        out = [{"symbol": p.symbol, "lots": p.signed_lots,
                "profit": getattr(p, "profit", 0.0)} for p in c.positions()]
        return {"available": True, "positions": out}
    finally:
        try:
            c.shutdown()
        except Exception:
            pass


@app.get("/api/bars/{symbol}")
def bars(symbol: str):
    """Recent bars + each model's projection cone for one symbol. Needs live
    MT5; degrades to {available:false} otherwise."""
    c = _mt5()
    if c is None:
        return {"available": False}
    try:
        import MetaTrader5 as mt5
        rates = c.bars(symbol, mt5.TIMEFRAME_M15, 120)
        if rates is None or len(rates) < 20:
            return {"available": False}
        closes = [float(x) for x in rates["close"]]
        times = [int(x) for x in rates["time"]]
        price = closes[-1]
        rets = [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))]
        vol = (sum(r * r for r in rets[-48:]) / max(len(rets[-48:]), 1)) ** 0.5
        last = data.latest_decision(data.load_decisions(LOG_DIR))
        view = next((v for v in (last or {}).get("views", []) if v["symbol"] == symbol), None)
        cones = {}
        if view:
            cones["consensus"] = projection.projection_cone(price, view["direction"], view["confidence"], vol)
            if view.get("claude"):
                cones["claude"] = projection.projection_cone(price, view["claude"]["direction"], view["claude"]["confidence"], vol)
            if view.get("nemotron"):
                cones["nemotron"] = projection.projection_cone(price, view["nemotron"]["direction"], view["nemotron"]["confidence"], vol)
        return {"available": True, "symbol": symbol, "times": times,
                "closes": closes, "vol": vol, "cones": cones}
    finally:
        try:
            c.shutdown()
        except Exception:
            pass


@app.get("/")
def index():
    return FileResponse(os.path.join(FRONTEND, "index.html"))


# static assets (styles.css, app.js) served from the frontend dir
app.mount("/static", StaticFiles(directory=FRONTEND), name="static")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python test_panel_api.py`
Expected: `OK`

- [ ] **Step 6: Manual live check (optional, needs MT5)**

Run: `python -m uvicorn panel.backend.app:app --port 8800` then in another shell `curl http://127.0.0.1:8800/api/state`. Expected: JSON with the latest decision. Stop with Ctrl+C. (If MT5 is running, `/api/positions` shows live positions; if not, `{"available": false}`.)

- [ ] **Step 7: Commit**

```bash
git add panel/backend/app.py panel/frontend/index.html test_panel_api.py
git commit -m "feat(panel): FastAPI backend — endpoints + static + optional MT5"
```

---

### Task 7: Frontend shell — SIGNAL//LAYER theme, layout, data glue

**Files:**
- Modify: `panel/frontend/index.html` (replace placeholder)
- Create: `panel/frontend/styles.css`
- Create: `panel/frontend/app.js`

**Interfaces:**
- Consumes: `GET /api/state`, `GET /api/equity` (rendered in later tasks). This task builds the shell + theme + a polling fetch loop and the top strip (regime + KPIs).

- [ ] **Step 1: Build the HTML shell**

Replace `panel/frontend/index.html` with:
```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SIGNAL//LAYER</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=IBM+Plex+Sans:wght@400;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js" charset="utf-8"></script>
  <link rel="stylesheet" href="/static/styles.css">
</head>
<body>
  <header id="strip">
    <div class="brand">SIGNAL<span>//</span>LAYER</div>
    <div id="regimes">
      <div class="regime claude"><label>CLAUDE</label><span id="regime-claude">—</span></div>
      <div class="regime nemo"><label>NEMOTRON</label><span id="regime-nemo">—</span></div>
    </div>
    <div id="kpis"></div>
  </header>
  <main>
    <section class="col-left">
      <div class="card" id="equity-card"><h2>Equity</h2><div id="equity-chart"></div></div>
      <div class="card" id="gauges-card"><h2>Risk</h2><div id="gauges"></div><div id="tripwires"></div></div>
      <div class="card" id="orders-card"><h2>Orders</h2><div id="orders"></div></div>
    </section>
    <section class="col-main">
      <div class="card" id="dumbbell-card"><h2>Consensus — latest</h2><div id="dumbbell"></div></div>
      <div class="card" id="heatmap-card"><h2>Consensus — over time</h2><div id="heatmap"></div></div>
      <div class="card" id="symbol-card"><h2>Instrument & projection</h2>
        <select id="symbol-select"></select><div id="symbol-chart"></div></div>
    </section>
  </main>
  <script src="/static/app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Build the theme**

Create `panel/frontend/styles.css`:
```css
:root{
  --canvas:#0D0E12; --surface:#14161D; --lift:#1E2330; --chartreuse:#C8FF00;
  --claude:#7B61FF; --nemo:#00C8A0; --human:#F5A623; --long:#2ECC71;
  --short:#FF4D4D; --flat:#3A3F50; --ink:#E8EAF0; --muted:#6B7080;
}
*{box-sizing:border-box}
body{margin:0;background:var(--canvas);color:var(--ink);
  font-family:"IBM Plex Sans",sans-serif;font-size:14px}
#strip{display:flex;align-items:center;gap:24px;height:56px;padding:0 18px;
  background:var(--surface);border-bottom:1px solid var(--lift);position:sticky;top:0;z-index:10}
.brand{font-family:"Space Grotesk";font-weight:700;letter-spacing:.04em;font-size:18px}
.brand span{color:var(--chartreuse)}
#regimes{display:flex;gap:18px;flex:1;min-width:0}
.regime{display:flex;flex-direction:column;min-width:0}
.regime label{font-size:10px;letter-spacing:.12em;color:var(--muted)}
.regime.claude span{color:var(--claude)} .regime.nemo span{color:var(--nemo)}
.regime span{font-family:"Space Grotesk";font-size:13px;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}
#kpis{display:flex;gap:18px;font-family:"JetBrains Mono"}
#kpis .kpi{display:flex;flex-direction:column;text-align:right}
#kpis .kpi label{font-size:9px;letter-spacing:.1em;color:var(--muted)}
#kpis .kpi b{font-weight:500;font-size:15px}
main{display:grid;grid-template-columns:38% 62%;gap:14px;padding:14px}
.col-left,.col-main{display:flex;flex-direction:column;gap:14px;min-width:0}
.card{background:var(--surface);border:1px solid var(--lift);border-radius:10px;padding:12px}
.card h2{margin:0 0 8px;font-size:10px;letter-spacing:.12em;font-weight:600;
  text-transform:uppercase;color:var(--muted)}
.mono{font-family:"JetBrains Mono"}
#orders div,#tripwires div{font-family:"JetBrains Mono";font-size:12px;padding:2px 0}
select{background:var(--lift);color:var(--ink);border:1px solid var(--muted);
  border-radius:6px;padding:4px 8px;font-family:"JetBrains Mono";margin-bottom:8px}
.tw{display:flex;justify-content:space-between}
.tw.active{color:var(--short)} .tw.ok{color:var(--muted)}
```

- [ ] **Step 3: Build the data glue + top strip**

Create `panel/frontend/app.js`:
```javascript
// SIGNAL//LAYER — read-only dashboard. Polls the backend and renders.
const C = { canvas:"#0D0E12", surface:"#14161D", lift:"#1E2330",
  chartreuse:"#C8FF00", claude:"#7B61FF", nemo:"#00C8A0", human:"#F5A623",
  long:"#2ECC71", short:"#FF4D4D", flat:"#3A3F50", ink:"#E8EAF0", muted:"#6B7080" };
const PLOT_BG = { paper_bgcolor:C.surface, plot_bgcolor:C.surface,
  font:{color:C.ink, family:"JetBrains Mono"}, margin:{l:40,r:12,t:8,b:28} };

async function getJSON(u){ const r = await fetch(u); return r.json(); }
const fmt = (n,d=0)=> n==null? "—" : Number(n).toLocaleString(undefined,{maximumFractionDigits:d,minimumFractionDigits:d});

function renderStrip(s){
  document.getElementById("regime-claude").textContent = s.regime?.claude || "—";
  document.getElementById("regime-nemo").textContent = s.regime?.nemotron || "— unavailable";
  const a = s.account || {};
  const kpis = [
    ["EQUITY", fmt(a.equity)], ["RET", a.return_pct!=null? (a.return_pct*100).toFixed(2)+"%":"—"],
    ["LEV", a.gross_leverage!=null? a.gross_leverage.toFixed(1)+"x":"—"],
    ["ML", a.margin_level!=null? Math.round(a.margin_level)+"%":"—"],
    ["DD", a.drawdown!=null? (a.drawdown*100).toFixed(1)+"%":"—"],
    ["SHARPE", a.sharpe!=null? a.sharpe.toFixed(2):"—"],
  ];
  document.getElementById("kpis").innerHTML = kpis.map(([k,v])=>
    `<div class="kpi"><label>${k}</label><b>${v}</b></div>`).join("");
}

async function tick(){
  const s = await getJSON("/api/state");
  renderStrip(s);
  if (window.renderCharts) await window.renderCharts(s);  // added in Tasks 8-9
}
tick();
setInterval(tick, 15000);
```

- [ ] **Step 4: Verify it renders**

Run: `python -m uvicorn panel.backend.app:app --port 8800` (with the repo's real `logs/` present, or temporarily set `PANEL_LOG_DIR` to the fixture dir). Open `http://127.0.0.1:8800/` in a browser.
Expected: the dark SIGNAL//LAYER shell loads, the top strip shows both regimes and the KPI row (EQUITY/RET/LEV/ML/DD/SHARPE) populated from the latest decision. Empty chart cards below (filled next). No console errors. Stop with Ctrl+C.

- [ ] **Step 5: Commit**

```bash
git add panel/frontend/index.html panel/frontend/styles.css panel/frontend/app.js
git commit -m "feat(panel): SIGNAL//LAYER shell, theme, and data glue"
```

---

### Task 8: Frontend charts — equity curve (with risk-floor lines), gauges, tripwires, orders

**Files:**
- Modify: `panel/frontend/app.js` (append a `renderCharts` and helpers)

**Interfaces:**
- Consumes: `GET /api/state` (gauges, tripwires, orders) and `GET /api/equity` (series). Defines `window.renderCharts(state)` called by `tick()`.

- [ ] **Step 1: Append the equity + gauges + tripwires + orders rendering**

Append to `panel/frontend/app.js`:
```javascript
function plotEquity(series){
  const xs = series.map(p=>p.ts), ys = series.map(p=>p.equity);
  const start = ys.length? ys[0] : 1000000;
  const killLine = start * (1 - 0.20);   // 20% drawdown kill level
  const traces = [
    {x:xs, y:ys, type:"scatter", mode:"lines", line:{color:C.chartreuse, shape:"hv", width:2}, name:"equity"},
    {x:xs, y:xs.map(()=>killLine), type:"scatter", mode:"lines",
     line:{color:C.short, dash:"dash", width:1}, name:"kill (-20%)"},
  ];
  Plotly.react("equity-chart", traces, {...PLOT_BG, height:180, showlegend:false}, {displayModeBar:false});
}

function bandSteps(bands){           // [{to,color}] -> Plotly gauge steps
  let from = 0;
  return bands.map(b => { const s = {range:[from, b.to], color:b.color}; from = b.to; return s; });
}
function plotGauges(gauges){
  const data = gauges.map((g,i)=>({
    type:"indicator", mode:"gauge+number", value:g.value,
    number:{suffix:g.unit, font:{size:16}},
    title:{text:g.label, font:{size:10, color:C.muted}},
    domain:{row:Math.floor(i/2), column:i%2},
    gauge:{axis:{range:[0,g.max], tickcolor:C.muted},
      bar:{color:C.ink, thickness:0.18},
      steps: bandSteps(g.bands),
      threshold:{line:{color:C.ink,width:2}, value: (g.hard[0] && g.hard[0].at) || g.max}},
  }));
  Plotly.react("gauges", data, {...PLOT_BG, height:220,
    grid:{rows:2, columns:2, pattern:"independent"}, margin:{l:10,r:10,t:24,b:10}}, {displayModeBar:false});
}

function renderTripwires(tw){
  document.getElementById("tripwires").innerHTML = tw.map(t=>
    `<div class="tw ${t.active?"active":"ok"}"><span>${t.label}</span>`+
    `<span>${t.active? "⚠ ACTIVE (penalty "+t.penalty+")":"ok"}</span></div>`).join("");
}

function renderOrders(orders){
  const el = document.getElementById("orders");
  if(!orders || !orders.length){ el.innerHTML = '<div style="color:var(--muted)">(none this refresh)</div>'; return; }
  el.innerHTML = orders.map(o=>{
    if(o.side==="close") return `<div>CLOSE ${o.symbol}</div>`;
    const col = o.side==="buy"? C.long : C.short;
    const sl = o.sl? `  SL=${o.sl}`:"";
    return `<div><span style="color:${col}">${o.side.toUpperCase()}</span> ${o.lots} ${o.symbol}  `+
      `(${o.cur.toFixed(2)}→${o.target.toFixed(2)})${sl}</div>`;
  }).join("");
}

window.renderCharts = async function(s){
  const eq = await getJSON("/api/equity");
  plotEquity(eq.series || []);
  plotGauges(s.gauges || []);
  renderTripwires(s.tripwires || []);
  renderOrders(s.orders || []);
  if (window.renderConsensus) await window.renderConsensus(s);  // Task 9
};
```

- [ ] **Step 2: Verify it renders**

Run the server (Task 7 Step 4) and reload `http://127.0.0.1:8800/`.
Expected: the equity card shows a chartreuse step-line with a dashed red "kill" line; the risk card shows four arc gauges (Margin Level, Gross Leverage, Margin Usage, Drawdown) with green/amber/red bands and the hard-threshold tick; the tripwire rows render (grey "ok" in the safe fixture); the orders card lists the latest order (BUY 1.56 USDJPY) or "(none)". No console errors.

- [ ] **Step 3: Commit**

```bash
git add panel/frontend/app.js
git commit -m "feat(panel): equity curve, risk gauges, tripwires, orders"
```

---

### Task 9: Frontend charts — consensus dumbbell, heatmap, per-symbol projection

**Files:**
- Modify: `panel/frontend/app.js` (append `renderConsensus` + symbol chart)

**Interfaces:**
- Consumes: `GET /api/state` (consensus), `GET /api/heatmap`, `GET /api/bars/{symbol}`. Defines `window.renderConsensus(state)`.

- [ ] **Step 1: Append the consensus dumbbell, heatmap, and symbol projection**

Append to `panel/frontend/app.js`:
```javascript
const dirColor = d => d==="long"? C.long : d==="short"? C.short : C.flat;

function plotDumbbell(rows){
  // one row per instrument: Claude (violet, left) — Nemotron (teal, right) confidences,
  // x sign encodes direction so opposing calls point opposite ways.
  const ys = rows.map(r=>r.symbol);
  const sgn = d => d==="long"?1 : d==="short"?-1 : 0;
  const cx = rows.map(r=> r.claude? sgn(r.claude.direction)*r.claude.confidence : 0);
  const nx = rows.map(r=> r.nemotron? sgn(r.nemotron.direction)*r.nemotron.confidence : null);
  const lines = rows.map((r,i)=>({x:[cx[i], nx[i]??cx[i]], y:[r.symbol,r.symbol], type:"scatter", mode:"lines",
    line:{color: r.agreement==="agree"? C.chartreuse : r.agreement==="disagree"? C.short : C.muted,
      width:2, dash: r.boosted? "solid":"dot"}, showlegend:false, hoverinfo:"skip"}));
  const claudePts = {x:cx, y:ys, type:"scatter", mode:"markers", name:"Claude",
    marker:{color:C.claude, size:11}, text:rows.map(r=>r.claude?.confidence?.toFixed(2)||""), hovertemplate:"Claude %{y}: %{x:.2f}<extra></extra>"};
  const nemoPts = {x:nx, y:ys, type:"scatter", mode:"markers", name:"Nemotron",
    marker:{color:C.nemo, size:11}, hovertemplate:"Nemotron %{y}: %{x:.2f}<extra></extra>"};
  Plotly.react("dumbbell", [...lines, claudePts, nemoPts],
    {...PLOT_BG, height: Math.max(120, ys.length*30+40), xaxis:{range:[-1.05,1.05], zeroline:true, zerolinecolor:C.muted, title:{text:"short ◀  confidence  ▶ long", font:{size:9}}},
     showlegend:false}, {displayModeBar:false});
}

function plotHeatmap(hm){
  const lum = c => c==null? 0 : c.confidence;             // 0..1
  const sign = c => c==null? 0 : (c.direction==="long"?1 : c.direction==="short"?-1 : 0);
  // z = signed confidence (-1..1): red short, green long, dark flat
  const z = hm.cells.map(row=> row.map(c=> c==null? null : sign(c)*Math.max(lum(c),0.05)));
  const text = hm.cells.map(row=> row.map(c=> c==null? "" :
    `${c.direction} ${c.confidence.toFixed(2)}${c.boosted?" ★":""}`));
  Plotly.react("heatmap", [{z, x:hm.timestamps.map(t=>t.slice(11,16)), y:hm.symbols,
    type:"heatmap", text, hovertemplate:"%{y} %{x}: %{text}<extra></extra>",
    colorscale:[[0,C.short],[0.5,C.canvas],[1,C.long]], zmid:0, showscale:false}],
    {...PLOT_BG, height: Math.max(160, hm.symbols.length*22+40), margin:{l:64,r:8,t:8,b:24}}, {displayModeBar:false});
}

async function plotSymbol(symbol){
  const b = await getJSON("/api/bars/"+symbol);
  const el = document.getElementById("symbol-chart");
  if(!b.available){ el.innerHTML = '<div style="color:var(--muted);padding:20px">live MT5 unavailable — projection needs a running terminal</div>'; return; }
  const n = b.closes.length;
  const xhist = b.closes.map((_,i)=>i);
  const price = [{x:xhist, y:b.closes, type:"scatter", mode:"lines", line:{color:C.muted,width:1}, name:symbol, hoverinfo:"skip"}];
  const coneTrace=(cone,color,name)=> cone? [
    {x:cone.upper.map(p=>n-1+p[0]), y:cone.upper.map(p=>p[1]), mode:"lines", line:{width:0}, showlegend:false, hoverinfo:"skip"},
    {x:cone.lower.map(p=>n-1+p[0]), y:cone.lower.map(p=>p[1]), mode:"lines", line:{width:0}, fill:"tonexty",
      fillcolor:color+"22", showlegend:false, hoverinfo:"skip"},
    {x:cone.mid.map(p=>n-1+p[0]), y:cone.mid.map(p=>p[1]), mode:"lines", line:{color:color,width:2}, name:name},
  ] : [];
  const traces = [...price,
    ...coneTrace(b.cones.claude, C.claude, "Claude"),
    ...coneTrace(b.cones.nemotron, C.nemo, "Nemotron"),
    ...coneTrace(b.cones.consensus, C.chartreuse, "Consensus")];
  Plotly.react("symbol-chart", traces, {...PLOT_BG, height:220, showlegend:true,
    legend:{font:{size:9}, orientation:"h"}}, {displayModeBar:false});
}

window.renderConsensus = async function(s){
  plotDumbbell(s.consensus || []);
  const hm = await getJSON("/api/heatmap");
  plotHeatmap(hm);
  const sel = document.getElementById("symbol-select");
  if(!sel.dataset.init && (s.consensus||[]).length){
    sel.innerHTML = s.consensus.map(r=>`<option>${r.symbol}</option>`).join("");
    sel.dataset.init = "1";
    sel.onchange = ()=>plotSymbol(sel.value);
    plotSymbol(sel.value);
  }
};
```

- [ ] **Step 2: Verify it renders**

Run the server and reload `http://127.0.0.1:8800/`.
Expected: the main column shows (a) the consensus **dumbbell** — Claude (violet) and Nemotron (teal) markers per instrument with a connecting line that is chartreuse-solid on boosted agreement, dotted on plain agreement, red on disagreement; (b) the **heatmap** of instruments × timesteps colored red→green by signed confidence; (c) the **instrument** dropdown — selecting a symbol shows recent price with forward **projection cones** in violet/teal/chartreuse (or the "live MT5 unavailable" note if no terminal). No console errors.

- [ ] **Step 3: Commit**

```bash
git add panel/frontend/app.js
git commit -m "feat(panel): consensus dumbbell, heatmap, per-symbol projection cones"
```

---

## Self-Review

**Spec coverage (Phase 1 only):**
- §6 Phase-1 panels → top strip + KPIs (Task 7); equity w/ risk-floor lines (Task 8); gauges + tripwire clocks (Task 8); consensus dumbbell + heatmap (Task 9); positions/orders (orders in Task 8; live positions endpoint Task 6 — a positions *table* widget is minor and folded into the orders/state display); AI cost (in `/api/state`, shown via KPIs — note cost panel itself is a light follow-up); per-symbol projection cones (Task 9 + projection.py Task 5 + `/api/bars` Task 6). ✅
- §5 visual system (palette/type/charts) → Task 7 theme + Tasks 8-9 charts. ✅
- §4 data sources → loaders Task 2, transforms Tasks 3-4, MT5-optional Task 6. ✅
- §1a safety (no engine changes, isolated branch, read-only) → enforced by Global Constraints; no file under `bot/` is touched by any task. ✅
- Data-gap fixes are explicitly Phase-2+ optional and NOT in this plan (correct — they edit `bot/`). ✅

**Placeholder scan:** no TBD/"add error handling"/"similar to". Every code step is complete. The gauge `steps` are built by a clean `bandSteps` helper (Task 8).

**Type consistency:** `load_decisions`/`latest_decision`/`equity_series` (Task 2) consumed by `app.py` (Task 6) with matching names. `consensus_rows`/`heatmap_matrix` (Task 3), `risk_gauges`/`tripwire_status` (Task 4), `projection_cone` (Task 5) all consumed by `app.py` with the exact signatures defined. Frontend `window.renderCharts`/`window.renderConsensus` defined in Tasks 8/9 and called by `tick()` (Task 7) / `renderCharts` (Task 8) — names match. API response keys (`account`, `gauges`, `tripwires`, `consensus`, `regime`, `orders`, `series`, `symbols`, `cells`, `cones`) are produced in Task 6 and consumed by Tasks 7-9 consistently.

## Notes for the implementer
- **Branch first:** `git checkout -b feat/control-panel` before Task 1. Never commit to the branch the operator runs.
- **Never touch `bot/`.** If any task seems to require an engine change, STOP and report — it belongs in a later phase, not Phase 1.
- **MT5-dependent steps** (`/api/positions`, `/api/bars`, the projection chart) only fully light up with the MetaTrader 5 terminal running; without it they must degrade to the "unavailable" state, never error.
