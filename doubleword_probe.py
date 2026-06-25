"""Quick latency/availability probe for the Doubleword (Real-Time) inference API.

Verifies the two things the engine-improvements spec flagged as UNVERIFIED:
  1. which models are actually served, and
  2. real-time chat-completion latency (the cheap Async/Batch tiers are 24h SLA
     and useless for a <=15-min signal loop).

Usage (PowerShell):  $env:DOUBLEWORD_API_KEY="..."; python doubleword_probe.py [model]
Usage (bash):        DOUBLEWORD_API_KEY=... python doubleword_probe.py [model]

It does NOT touch the trading engine; pure read-only API calls.
"""
from __future__ import annotations

import os
import sys
import time

import httpx

BASE = os.getenv("DOUBLEWORD_BASE_URL", "https://api.doubleword.ai/v1").rstrip("/")
KEY = os.getenv("DOUBLEWORD_API_KEY")


def _headers():
    return {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}


def list_models():
    with httpx.Client(timeout=20.0) as c:
        r = c.get(f"{BASE}/models", headers=_headers())
        r.raise_for_status()
        data = r.json().get("data", [])
    ids = [m.get("id") for m in data]
    print(f"\n{len(ids)} model(s) served at {BASE}:")
    for i in ids:
        print(f"  - {i}")
    return ids


def time_completion(model: str, n: int = 3):
    """Send a tiny JSON-mode chat completion n times; report latencies."""
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a terse trading assistant."},
            {"role": "user", "content": 'Reply with JSON only: {"ok": true, '
             '"view": "long|short|flat for EURUSD", "confidence": 0.0}'},
        ],
        "temperature": 0.2,
        "max_tokens": 200,
        "response_format": {"type": "json_object"},
    }
    lat = []
    with httpx.Client(timeout=60.0) as c:
        for k in range(n):
            t0 = time.time()
            r = c.post(f"{BASE}/chat/completions", headers=_headers(), json=body)
            dt = time.time() - t0
            r.raise_for_status()
            resp = r.json()
            lat.append(dt)
            u = resp.get("usage", {}) or {}
            content = resp["choices"][0]["message"]["content"].replace("\n", " ")
            print(f"  call {k+1}: {dt:.2f}s  "
                  f"in={u.get('prompt_tokens','?')} out={u.get('completion_tokens','?')}  "
                  f"-> {content[:90]}")
    if lat:
        print(f"\nlatency: min={min(lat):.2f}s  avg={sum(lat)/len(lat):.2f}s  max={max(lat):.2f}s")
        print("verdict: REAL-TIME OK for a 15-min loop" if max(lat) < 30
              else "verdict: SLOW — check whether this is the real-time tier")


def main():
    if not KEY:
        print("DOUBLEWORD_API_KEY not set. Get a free key at https://app.doubleword.ai "
              "then re-run:  $env:DOUBLEWORD_API_KEY=\"...\"; python doubleword_probe.py")
        return 1
    try:
        ids = list_models()
    except Exception as e:
        print(f"model list failed: {e}")
        return 2
    model = sys.argv[1] if len(sys.argv) > 1 else (ids[0] if ids else None)
    if not model:
        print("no model to test.")
        return 3
    print(f"\nprobing chat latency on: {model}")
    try:
        time_completion(model)
    except Exception as e:
        print(f"completion failed: {e}")
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
