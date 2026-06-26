"""Ensemble voter routes through the gateway /responses path (not /chat/completions,
which Doubleword 400s on) and parses the JSON view. No network — gateway is stubbed."""
import bot.gateway_ensemble as ge
from bot import gateway

_calls = {}


def _fake_complete_responses(chat_body, timeout=120.0):
    _calls["model"] = chat_body["model"]
    _calls["url"] = "responses"     # proves we went through the /responses path
    return {"output_text": '{"regime":"risk-off","views":[{"symbol":"EURUSD",'
            '"direction":"short","confidence":0.7,"rationale":"x"}]}'}


gateway.complete_responses = _fake_complete_responses
gateway.api_key = lambda: "stub-key"      # treat the gateway as configured

# one model's (views, regime)
views, regime = ge.get_model_view("deepseek-ai/DeepSeek-V4-Pro", "ctx", ["EURUSD"])
assert _calls["model"] == "deepseek-ai/DeepSeek-V4-Pro"
assert views["EURUSD"][0] == -1 and abs(views["EURUSD"][1] - 0.7) < 1e-9
assert regime == "risk-off"

# get_ensemble_views returns {model: (views, regime)}
out = ge.get_ensemble_views("ctx", ["EURUSD"], ["deepseek-ai/DeepSeek-V4-Pro"])
v, rg = out["deepseek-ai/DeepSeek-V4-Pro"]
assert v["EURUSD"][0] == -1 and rg == "risk-off"

# no models -> {} ; unconfigured gateway -> {}
assert ge.get_ensemble_views("ctx", ["EURUSD"], []) == {}
def _raise(): raise RuntimeError("no key")
gateway.api_key = _raise
assert ge.get_ensemble_views("ctx", ["EURUSD"], ["deepseek-ai/DeepSeek-V4-Pro"]) == {}

print("OK")
