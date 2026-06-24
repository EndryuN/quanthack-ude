"""Per-symbol operator overrides — pure logic, no MT5. The panel writes
state/overrides.json; the engine reads it (gated behind use_control_panel).
Schema per symbol: {pinned: bool, view: {direction, confidence}|null,
take_profit: float|null, note: str}."""
from __future__ import annotations

import json
import os


def load_overrides(state_dir: str) -> dict:
    """{sym: {...}} from state/overrides.json. Missing/corrupt -> {}."""
    path = os.path.join(state_dir, "overrides.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def pinned_set(overrides: dict) -> set:
    return {s for s, o in overrides.items() if isinstance(o, dict) and o.get("pinned")}


def human_views(overrides: dict) -> dict:
    """{sym: {direction, confidence}} for symbols with a non-null view."""
    out = {}
    for s, o in overrides.items():
        if isinstance(o, dict) and o.get("view"):
            out[s] = o["view"]
    return out


def take_profits(overrides: dict) -> dict:
    """{sym: tp_price} for symbols with a non-null take_profit."""
    out = {}
    for s, o in overrides.items():
        if isinstance(o, dict) and o.get("take_profit") is not None:
            out[s] = float(o["take_profit"])
    return out
