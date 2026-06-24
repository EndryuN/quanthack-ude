"""Nemotron second opinion via NVIDIA's hosted OpenAI-compatible API, plus the
pure consensus rule that merges it with Claude's primary view.

Claude always sets direction; Nemotron can only RAISE confidence when it
independently agrees — it never vetoes. The second opinion is strictly
additive: if it is unavailable, trading proceeds exactly as Claude-only."""
from __future__ import annotations

import json
import os

import httpx

from .ai_signal import SYSTEM, _DIR

_DIR_STR = {1: "long", -1: "short", 0: "flat"}


def apply_consensus(claude_views, claude_rationale, nemotron_views, *, gain=0.5):
    """Merge Claude (primary, authoritative direction) with Nemotron (booster).

    Returns (sizer_views, view_records):
      sizer_views:  {sym: (dir_int, final_conf)}  -- fed to the sizer
      view_records: list of dicts in the decision-log `views[]` shape
    """
    available = bool(nemotron_views)
    sizer_views = {}
    records = []
    for sym, (dc, cc) in claude_views.items():
        nv = nemotron_views.get(sym) if available else None
        final = cc
        boosted = False
        if not available:
            agreement = "second_opinion_unavailable"
        elif nv is None or nv[0] == 0:
            agreement = "nemotron_flat"
        elif nv[0] == dc and dc != 0:
            agreement = "agree"
            final = cc + (1.0 - cc) * gain * nv[1]
            boosted = True
        else:
            agreement = "disagree"

        final = max(0.0, min(1.0, final))
        sizer_views[sym] = (dc, final)
        records.append({
            "symbol": sym,
            "direction": _DIR_STR[dc],
            "confidence": round(final, 4),
            "claude": {"direction": _DIR_STR[dc], "confidence": cc,
                       "rationale": claude_rationale.get(sym, "")},
            "nemotron": ({"direction": _DIR_STR[nv[0]], "confidence": nv[1],
                          "rationale": nv[2]} if nv is not None else None),
            "agreement": agreement,
            "boosted": boosted,
        })
    return sizer_views, records


NVIDIA_BASE_URL = os.getenv("NVIDIA_BASE_URL",
                            "https://integrate.api.nvidia.com/v1")


def parse_second_opinion(content: str, symbols):
    """Parse a Nemotron JSON response into ({sym:(dir,conf,rationale)}, regime).
    Tolerant of ```code fences``` and surrounding prose."""
    requested = set(symbols)
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text[:4].lower() == "json":
            text = text[4:]
    i, j = text.find("{"), text.rfind("}")
    if i == -1 or j == -1:
        raise ValueError("no JSON object in second-opinion response")
    obj = json.loads(text[i:j + 1])
    regime = str(obj.get("regime", ""))
    views = {}
    for v in obj.get("views", []):
        sym = v.get("symbol")
        if sym in requested:
            d = _DIR.get(str(v.get("direction", "flat")).lower(), 0)
            conf = max(0.0, min(1.0, float(v.get("confidence", 0.0))))
            views[sym] = (d, conf, str(v.get("rationale", "")))
    return views, regime


def get_second_opinion(context: str, symbols, model: str, *, timeout: float = 30.0):
    """Blind Nemotron second opinion on the same market context Claude saw.
    Returns (views, regime, usage). Raises on any failure (missing key, HTTP
    error, unparseable body) so the caller degrades to Claude-only."""
    key = os.getenv("NVIDIA_API_KEY")
    if not key:
        raise RuntimeError("NVIDIA_API_KEY not set")
    user = (
        f"Instruments (rate exactly these {len(symbols)}): {', '.join(symbols)}\n\n"
        f"Recent multi-timeframe action:\n{context}\n\n"
        "Respond with ONLY a JSON object of this exact shape, no prose:\n"
        '{"regime": "<one line>", "views": [{"symbol": "EURUSD", '
        '"direction": "long|short|flat", "confidence": 0.0, '
        '"rationale": "<one concise sentence>"}]}'
    )
    body = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": user}],
        "temperature": 0.2,
        "max_tokens": 4000,
        "response_format": {"type": "json_object"},
        # Nemotron-3-Super is a reasoning model. Disable extended thinking so it
        # returns the JSON directly: a 15-min signal call must be fast, fit in
        # max_tokens, and not blow the timeout. (NVIDIA NIM reads this the same
        # way the OpenAI SDK's extra_body does.)
        "chat_template_kwargs": {"enable_thinking": False},
    }
    with httpx.Client(timeout=timeout) as c:
        r = c.post(f"{NVIDIA_BASE_URL.rstrip('/')}/chat/completions",
                   headers={"Authorization": f"Bearer {key}",
                            "Content-Type": "application/json"},
                   json=body)
        r.raise_for_status()
        resp = r.json()
    content = resp["choices"][0]["message"]["content"]
    views, regime = parse_second_opinion(content, symbols)
    u = resp.get("usage", {}) or {}
    usage = {"in": u.get("prompt_tokens", 0),
             "out": u.get("completion_tokens", 0), "usd": 0.0}
    return views, regime, usage


def apply_human_override(sizer_views, records, overrides):
    """After apply_consensus, let a human view be the DECIDING voice for a symbol
    — it can flip the AI's direction. `overrides` is {sym: {direction, confidence}}
    (already filtered to non-null human views). Mutates sizer_views + records in
    place and returns them."""
    by_sym = {r["symbol"]: r for r in records}
    for sym, hv in overrides.items():
        d = _DIR.get(str(hv.get("direction", "flat")).lower(), 0)
        c = max(0.0, min(1.0, float(hv.get("confidence", 0.0))))
        if sym in sizer_views:
            sizer_views[sym] = (d, c)
        rec = by_sym.get(sym)
        if rec is not None:
            rec["direction"] = _DIR_STR[d]
            rec["confidence"] = round(c, 4)
            rec["human"] = {"direction": _DIR_STR[d], "confidence": c}
            rec["agreement"] = "human_override"
            rec["boosted"] = False
    return sizer_views, records
