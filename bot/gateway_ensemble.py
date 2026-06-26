"""Query an ensemble of models through the Pydantic AI Gateway -> Doubleword for
additive second opinions. One blind call per model on the SAME market context
Claude saw; each model that fails is dropped from the vote.

Routed through the gateway's **/responses** endpoint (reusing bot/gateway.py):
the gateway injects a `safety_identifier` onto /chat/completions that Doubleword
400s on, so we convert our chat body and post to /responses instead. Auth + base
URL come from bot/gateway.py (PYDANTIC_AI_GATEWAY_API_KEY) — no separate config.

Model ids come from EngineConfig.ensemble_models. No-op (returns {}) when the
gateway key isn't configured, so the engine degrades to Claude(+Nemotron) exactly
as before. Strictly additive: a voter can only raise confidence on agreement.
"""
from __future__ import annotations

from . import gateway
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


def get_model_view(model: str, context: str, symbols, *, timeout: float = 30.0):
    """One model's ({sym: (dir, conf, rationale)}, regime). Raises on any failure.
    Routed through the gateway /responses path (reuses gateway.complete_responses)."""
    chat_body = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": _user_prompt(symbols, context)}],
        "temperature": 0.2,
        # DeepSeek-V4-Pro REASONS before answering: a full-universe call spends
        # ~4.5k output tokens (CoT + the JSON), so a tight cap truncates the JSON
        # and the parse fails with "no JSON object". Give it ample headroom.
        "max_tokens": 12000,
    }
    resp = gateway.complete_responses(chat_body, timeout=timeout)
    content = gateway.content_of_responses(resp)
    views, regime = parse_second_opinion(content, symbols)
    return views, regime


def get_ensemble_views(context: str, symbols, models, *, timeout: float = 30.0) -> dict:
    """Poll every model in `models` through the gateway. Returns
    {model_id: (views, regime)}; models that error are omitted. Returns {} when
    the gateway key isn't configured or `models` is empty."""
    if not models:
        return {}
    try:
        gateway.api_key()                      # gateway configured?
    except Exception:
        return {}
    out = {}
    for m in models:
        try:
            out[m] = get_model_view(m, context, symbols, timeout=timeout)
        except Exception as e:
            print(f"        [ensemble] {m} failed: {e}")
    return out
