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
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue   # skip a half-written/corrupt line (engine appends concurrently)
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
    if not isinstance(side, dict):
        return None
    return {"direction": side.get("direction", "flat"), "confidence": side.get("confidence")}


def _deepseek_of(v: dict) -> dict | None:
    """Slim view of the first DeepSeek gateway voter in a logged view record
    (record.views[i].voters = {model_id: {...}}), or None if it didn't vote."""
    for name, vv in (v.get("voters") or {}).items():
        if vv and "deepseek" in name.lower():
            return {"direction": vv["direction"], "confidence": vv["confidence"]}
    return None


def normalize_regime(regime: dict) -> dict:
    """Map the logged regime (keys: claude, nemotron, <gateway model ids>) to the
    short labels the panel uses: claude / nemotron / deepseek."""
    out = {"claude": regime.get("claude"), "nemotron": regime.get("nemotron")}
    for k, val in (regime or {}).items():
        if "deepseek" in k.lower():
            out["deepseek"] = val
    return out


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
            "deepseek": _deepseek_of(v),
            "agreement": v["agreement"],
            "boosted": v.get("boosted", False),
        })
    rows.sort(key=lambda r: -r["confidence"])
    return rows


def heatmap_matrix(records: list[dict]) -> dict:
    """instruments x timesteps grid. cells[r][c] is the view dict or None."""
    decisions = [r for r in records if r.get("event") != "refresh_failed"]
    # keep the over-time grid readable + light: downsample to <= MAX columns,
    # preserving the full time span (every k-th refresh). Otherwise hundreds of
    # 1-2px columns render washed-out/blank and bloat the payload.
    MAX = 180
    if len(decisions) > MAX:
        k = (len(decisions) + MAX - 1) // MAX
        decisions = decisions[::k]
    symbols: list[str] = []
    for rec in decisions:
        for v in rec.get("views", []):
            s = v.get("symbol")
            if s and s not in symbols:
                symbols.append(s)
    timestamps = [rec.get("ts", "") for rec in decisions]
    cells = [[None] * len(decisions) for _ in symbols]
    sidx = {s: i for i, s in enumerate(symbols)}
    for c, rec in enumerate(decisions):
        for v in rec.get("views", []):
            s = v.get("symbol")
            if s not in sidx:
                continue
            used = {"direction": v.get("direction", "flat"), "confidence": v.get("confidence")}
            claude = _slim(v.get("claude")) or used        # old records: fall back to `used`
            nemo = _slim(v.get("nemotron"))                 # None when no second opinion
            combined = _slim(v.get("combined")) or used     # old records: `used` was the combined
            cells[sidx[s]][c] = {
                "direction": v.get("direction", "flat"), "confidence": v.get("confidence"),
                "agreement": v.get("agreement", ""), "boosted": v.get("boosted", False),
                "claude": claude, "nemotron": nemo, "combined": combined,
                "deepseek": _deepseek_of(v),
            }
    return {"symbols": symbols, "timestamps": timestamps, "cells": cells}


from bot.config import RiskLimits  # read-only import of the limit constants

_LIM = RiskLimits()
_GREEN, _AMBER, _RED = "#2ECC71", "#F5A623", "#FF4D4D"

# Competition tripwire penalty thresholds — fixed contest rules, not in RiskLimits.
_LEV_PENALTY = 28        # gross-leverage penalty level (x)
_MARGIN_PENALTY = 0.90   # margin-usage penalty level (fraction)
_DD_WARN_PCT = 10.0      # drawdown green->amber boundary (%)


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
         "hard": [{"at": _LIM.max_gross_leverage, "label": "cap"}, {"at": _LEV_PENALTY, "label": "penalty"}]},
        {"key": "margin_usage", "label": "Margin Usage", "value": mu, "unit": "%",
         "max": 100,
         "bands": [{"to": _LIM.max_margin_usage * 100 * 0.83, "color": _GREEN},
                   {"to": _LIM.max_margin_usage * 100, "color": _AMBER},
                   {"to": 100, "color": _RED}],
         "hard": [{"at": _LIM.max_margin_usage * 100, "label": "cap"}, {"at": _MARGIN_PENALTY * 100, "label": "penalty"}]},
        {"key": "drawdown", "label": "Drawdown", "value": dd, "unit": "%",
         "max": 30,
         "bands": [{"to": _DD_WARN_PCT, "color": _GREEN},
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
         "active": lev > 25, "warn": 25, "penalty": _LEV_PENALTY},
        {"key": "margin", "label": "Margin > 85%", "value": mu,
         "active": mu > 0.85, "warn": 0.85, "penalty": _MARGIN_PENALTY},
    ]


def recent_orders(records: list[dict], limit: int = 12) -> list[dict]:
    """Most-recent orders across ALL decision records (newest first), each tagged
    with the `ts` of the refresh that placed it. The per-refresh `orders` field
    is usually empty (orders fire only when the signal changes), so this rolling
    feed is what the panel's Orders pane shows."""
    out = []
    for rec in reversed(records):
        if rec.get("event") == "refresh_failed":
            continue
        for o in rec.get("orders", []):
            out.append({**o, "ts": rec.get("ts")})
            if len(out) >= limit:
                return out
    return out
