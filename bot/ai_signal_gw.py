"""Gateway (OpenAI-compatible) variant of the AI signal.

Reuses the LIVE prompt and schema verbatim — the SAME `SYSTEM` text, the SAME
`build_context()` snapshot, and the SAME `MarketView` Pydantic schema as
bot/ai_signal.py — so a backtest exercises the real signal logic, just routed
through the Pydantic AI Gateway -> Doubleword instead of the Anthropic API.

Two things live here:
  * build_request_body()  -> a full OpenAI chat-completions request. Shared by
    the realtime path AND by the batch-JSONL exporter (one body per line).
  * parse_views()         -> response text -> {symbol: (direction, conf)} dict,
    validated through the same MarketView schema the live engine uses.

NOTE: the gateway model (DeepSeek V4 Flash by default) is NOT the live model
(Claude Opus 4.8). This validates the prompt + architecture on a cheaper model;
it is not an apples-to-apples backtest of the live signal. See docs/AI_BACKTEST.md.
"""
from __future__ import annotations

import json

from pydantic import ValidationError

from .ai_signal import SYSTEM, MarketView, _DIR, build_context  # reuse live defs
from . import gateway


def _user_msg(symbols, context: str) -> str:
    return (f"Instruments and recent action (competition universe = exactly "
            f"these {len(symbols)} symbols):\n\n{context}\n\n"
            f"Give a directional view + calibrated confidence for each. "
            f"Respond ONLY with a JSON object matching this schema: "
            f"{{\"regime\": str, \"views\": [{{\"symbol\": str, "
            f"\"direction\": \"long\"|\"short\"|\"flat\", "
            f"\"confidence\": number in [0,1], \"rationale\": str}}]}}.")


def build_request_body(symbols, context: str, model: str | None = None,
                       service_tier: str | None = None,
                       max_tokens: int = 4000) -> dict:
    """Full OpenAI-style request. `service_tier` selects the Doubleword pricing
    tier: None/"priority"=realtime, "flex"=async. (Batch uses the JSONL file
    mechanism, not this field.)"""
    body = {
        "model": model or gateway.DEFAULT_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": _user_msg(symbols, context)},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.2,
        # Ask for a JSON object. We validate against MarketView ourselves so we
        # stay tolerant of providers that don't enforce strict json_schema.
        "response_format": {"type": "json_object"},
    }
    if service_tier:
        body["service_tier"] = service_tier
    return body


def _strip_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1] if "\n" in s else s
        if s.endswith("```"):
            s = s.rsplit("```", 1)[0]
        # drop a possible leading "json" tag
        s = s.lstrip()
        if s[:4].lower() == "json":
            s = s[4:].lstrip()
    return s.strip()


def parse_views(content: str, symbols) -> tuple[dict, MarketView | None]:
    """content (assistant text) -> ({symbol: (dir_int, conf)}, MarketView).
    Returns ({...all flat...}, None) if the text can't be parsed."""
    raw = _strip_fences(content)
    try:
        mv = MarketView.model_validate_json(raw)
    except ValidationError:
        # last resort: find the outermost JSON object in the text
        try:
            start, end = raw.index("{"), raw.rindex("}") + 1
            mv = MarketView.model_validate(json.loads(raw[start:end]))
        except Exception:
            return ({s: (0, 0.0) for s in symbols}, None)

    requested = set(symbols)
    views = {}
    for v in mv.views:
        if v.symbol in requested:
            views[v.symbol] = (_DIR.get(v.direction, 0), float(v.confidence))
    for s in symbols:
        views.setdefault(s, (0, 0.0))
    return views, mv


def get_views_gw(symbols, model: str | None = None,
                 service_tier: str | None = None, context: str | None = None,
                 client=None) -> tuple[dict, MarketView | None, dict]:
    """Realtime single call through the gateway. `client` is an MT5Client (used
    only to build the live context); pass `context` directly to skip that.
    Returns (views, MarketView, usage)."""
    if context is None:
        if client is None:
            raise ValueError("provide either `context` or a live `client`")
        context = build_context(client, symbols)
    body = build_request_body(symbols, context, model=model,
                              service_tier=service_tier)
    # route via /responses (the /chat/completions proxy injects a
    # safety_identifier param Doubleword rejects)
    resp = gateway.complete_responses(body)
    views, mv = parse_views(gateway.content_of_responses(resp), symbols)
    return views, mv, gateway.usage_of_responses(resp)
