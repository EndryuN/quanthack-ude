"""Query an ensemble of models through an OpenAI-compatible gateway (the Pydantic
AI Gateway / Logfire, which fronts Doubleword inference) for additive second
opinions. One blind call per model on the same market context Claude saw; each
model that fails is simply dropped from the vote.

Config via env (so no secrets in code):
  GATEWAY_BASE_URL   e.g. the Pydantic AI Gateway OpenAI-compatible base URL
  GATEWAY_API_KEY    the gateway / Logfire token
The model ids to poll come from EngineConfig.ensemble_models.

No-op (returns {}) when the gateway isn't configured, so the engine degrades to
Claude-only exactly as before. Uses httpx + the tolerant parser already proven
for the Nemotron path; can be swapped to the pydantic-ai client later for Logfire
tracing without changing callers.
"""
from __future__ import annotations

import os

import httpx

from .ai_signal import SYSTEM
from .second_opinion import parse_second_opinion


def _user_prompt(symbols, context: str) -> str:
    return (
        f"Instruments (rate exactly these {len(symbols)}): {', '.join(symbols)}\n\n"
        f"Recent multi-timeframe action:\n{context}\n\n"
        "Respond with ONLY a JSON object of this exact shape, no prose:\n"
        '{"regime": "<one line>", "views": [{"symbol": "EURUSD", '
        '"direction": "long|short|flat", "confidence": 0.0, '
        '"rationale": "<one concise sentence>"}]}'
    )


def get_model_view(model: str, context: str, symbols, *, base_url: str, key: str,
                   timeout: float = 30.0):
    """One model's views: {sym: (dir, conf, rationale)}. Raises on any failure."""
    body = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": _user_prompt(symbols, context)}],
        "temperature": 0.2,
        "max_tokens": 4000,
        "response_format": {"type": "json_object"},
    }
    with httpx.Client(timeout=timeout) as c:
        r = c.post(f"{base_url.rstrip('/')}/chat/completions",
                   headers={"Authorization": f"Bearer {key}",
                            "Content-Type": "application/json"},
                   json=body)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
    views, _regime = parse_second_opinion(content, symbols)
    return views


def get_ensemble_views(context: str, symbols, models, *, base_url: str = None,
                       key: str = None, timeout: float = 30.0) -> dict:
    """Poll every model in `models`. Returns {model_id: {sym: (dir, conf, rationale)}};
    models that error are omitted. Returns {} when the gateway is unconfigured."""
    base = (base_url if base_url is not None else os.getenv("GATEWAY_BASE_URL", "")).strip()
    k = (key if key is not None else os.getenv("GATEWAY_API_KEY", "")).strip()
    if not base or not k or not models:
        return {}
    out = {}
    for m in models:
        try:
            out[m] = get_model_view(m, context, symbols, base_url=base, key=k,
                                    timeout=timeout)
        except Exception as e:
            print(f"        [ensemble] {m} failed: {e}")
    return out
