"""Thin OpenAI-compatible client for the Pydantic AI Gateway -> Doubleword.

We talk to the gateway over plain HTTP with httpx (no openai SDK dependency).
The gateway exposes the standard OpenAI Chat Completions shape at
    {BASE_URL}/chat/completions
so the same request body works for realtime calls AND for batch JSONL lines.

Auth: Bearer token from PYDANTIC_AI_GATEWAY_API_KEY (loaded from .env by
bot/__init__.py). Never hardcode the key.

Doubleword offers realtime / async (service_tier="flex") / batch tiers; this
client lets the caller pick the tier per request so the backtester can run cheap.
"""
from __future__ import annotations

import os

import httpx

# From the gateway "Connect" tab. The OpenAI-style path /chat/completions is
# appended by the caller; this is the proxy root for the doubleword provider.
BASE_URL = os.getenv(
    "PYDANTIC_AI_GATEWAY_URL",
    "https://gateway-eu.pydantic.dev/proxy/doubleword",
)

# Default model available on the connected gateway. Override via env if the
# catalog changes (e.g. a model that also serves Doubleword's batch tier).
DEFAULT_MODEL = os.getenv("DOUBLEWORD_MODEL", "deepseek-ai/DeepSeek-V4-Flash")


def api_key() -> str:
    k = os.getenv("PYDANTIC_AI_GATEWAY_API_KEY")
    if not k:
        raise RuntimeError(
            "PYDANTIC_AI_GATEWAY_API_KEY not set — add it to .env "
            "(see .env.example).")
    return k


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {api_key()}",
        "Content-Type": "application/json",
    }


def chat_url() -> str:
    return f"{BASE_URL.rstrip('/')}/chat/completions"


def responses_url() -> str:
    return f"{BASE_URL.rstrip('/')}/responses"


# The gateway injects a `safety_identifier` parameter onto /chat/completions that
# Doubleword's backend rejects (400). The /responses endpoint is unaffected, so
# we route through it and convert our standard chat bodies on the fly.
def chat_to_responses_body(chat_body: dict) -> dict:
    msgs = chat_body.get("messages", [])
    system = next((m["content"] for m in msgs if m["role"] == "system"), None)
    user = next((m["content"] for m in msgs if m["role"] == "user"), "")
    rb = {
        "model": chat_body["model"],
        "input": user,
        "max_output_tokens": chat_body.get("max_tokens", 3000),
    }
    if system:
        rb["instructions"] = system
    if "temperature" in chat_body:
        rb["temperature"] = chat_body["temperature"]
    return rb


def content_of_responses(resp: dict) -> str:
    """Extract assistant text from a Responses-API result."""
    if resp.get("output_text"):
        return resp["output_text"]
    for o in resp.get("output", []):
        if o.get("type") == "message":
            return "".join(p.get("text", "") for p in o.get("content", []))
    return ""


def usage_of_responses(resp: dict) -> dict:
    u = resp.get("usage", {}) or {}
    gw = (u.get("pydantic_ai_gateway") or {})
    return {
        "in": u.get("input_tokens", 0),
        "out": u.get("output_tokens", 0),
        "cost": gw.get("cost_estimate", 0.0),
    }


def complete_responses(chat_body: dict, timeout: float = 120.0) -> dict:
    with httpx.Client(timeout=timeout) as c:
        r = c.post(responses_url(), headers=_headers(),
                   json=chat_to_responses_body(chat_body))
        r.raise_for_status()
        return r.json()


async def complete_responses_async(client: httpx.AsyncClient, chat_body: dict) -> dict:
    r = await client.post(responses_url(), headers=_headers(),
                          json=chat_to_responses_body(chat_body))
    r.raise_for_status()
    return r.json()


def complete(body: dict, timeout: float = 120.0) -> dict:
    """Single synchronous chat-completion. `body` is a full OpenAI request
    (model, messages, response_format, ...). Returns the parsed JSON response."""
    with httpx.Client(timeout=timeout) as c:
        r = c.post(chat_url(), headers=_headers(), json=body)
        r.raise_for_status()
        return r.json()


async def complete_async(client: httpx.AsyncClient, body: dict) -> dict:
    r = await client.post(chat_url(), headers=_headers(), json=body)
    r.raise_for_status()
    return r.json()


def content_of(resp: dict) -> str:
    """Pull the assistant message text out of an OpenAI-shaped response."""
    return resp["choices"][0]["message"]["content"]


def usage_of(resp: dict) -> dict:
    return resp.get("usage", {}) or {}
