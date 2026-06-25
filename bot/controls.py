"""Operator control channel — pure logic, no MT5. The panel writes
state/controls.json + state/presets.json; the engine reads controls and writes
state/control_ack.json. Everything here is gated by EngineConfig.use_control_panel
at the call sites; this module itself is side-effect-free except atomic_write_json."""
from __future__ import annotations

import json
import os

DEFAULT_CONTROLS = {
    "paused": False,        # True -> reconcile opens no new risk (de-risk still runs)
    "aggression": None,     # None -> use CONFIG.aggression; else override the sizer dial
    "block": [],            # symbols clamped toward flat (no new/increased risk)
    "allow": None,          # if a list, every symbol NOT listed is clamped toward flat
    "active_preset": "Default",
    "second_opinion": None, # None -> use CONFIG.use_second_opinion; True/False -> override
    "flatten_seq": 0,       # bump -> engine flattens all once
    "apply_seq": 0,         # bump -> engine forces a reconcile next tick (apply knobs now)
}

DEFAULT_ACK = {"flatten_seq_done": 0, "apply_seq_done": 0}

# Named risk postures along the existing sizer aggression axis (survival<->finals).
DEFAULT_PRESETS = {
    "Default":  {"aggression": 0.30, "block": [], "allow": None},
    "Survival": {"aggression": 0.0,  "block": [], "allow": None},
    "Finals":   {"aggression": 1.0,  "block": [], "allow": None},
}


def _load_json(path: str, default: dict) -> dict:
    out = dict(default)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            out.update(data)
    except (OSError, ValueError):
        pass
    return out


def load_controls(state_dir: str) -> dict:
    return _load_json(os.path.join(state_dir, "controls.json"), DEFAULT_CONTROLS)


def load_ack(state_dir: str) -> dict:
    return _load_json(os.path.join(state_dir, "control_ack.json"), DEFAULT_ACK)


def atomic_write_json(path: str, obj) -> None:
    """Write JSON via a temp file + atomic rename (the bot/state.py pattern)."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


def derisk_clamp(target: float, cur: float) -> float:
    """Clamp `target` toward flat: forbid opening/increasing/flipping, allow
    reduction. Byte-identical in behavior to engine._derisk_only per symbol."""
    if cur == 0:
        return 0.0
    if _sign(target) != _sign(cur):
        return 0.0            # flip or to-flat -> close
    if abs(target) > abs(cur):
        return cur            # don't increase
    return target             # reduction allowed


def blocked_set(controls: dict, all_symbols) -> set:
    """Symbols that should be de-risk-clamped: the block list, plus (when an
    allow-list is set) every symbol not on it."""
    s = set(controls.get("block") or [])
    allow = controls.get("allow")
    if allow is not None:
        allow = set(allow)
        s |= {sym for sym in all_symbols if sym not in allow}
    return s


def effective_aggression(controls: dict, default: float) -> float:
    a = controls.get("aggression")
    return default if a is None else float(a)


def second_opinion_on(controls, config_default: bool, key_present: bool) -> bool:
    """Effective Nemotron second-opinion setting. A control override (True/False)
    wins over the config default; either way it can never be on without an API
    key. `controls` may be None (panel gated off) -> use the config default."""
    pref = config_default
    if controls is not None and controls.get("second_opinion") is not None:
        pref = bool(controls["second_opinion"])
    return pref and key_present
