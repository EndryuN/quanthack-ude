"""Parser tolerates code fences/extra prose, clamps confidence, drops unknown
symbols. Network smoke runs only when NVIDIA_API_KEY is set."""
import os

from bot.second_opinion import parse_second_opinion

syms = ["EURUSD", "AUDUSD", "USDJPY"]
content = '''```json
{"regime":"USD strength","views":[
  {"symbol":"EURUSD","direction":"short","confidence":0.45,"rationale":"x"},
  {"symbol":"AUDUSD","direction":"SHORT","confidence":1.4,"rationale":"y"},
  {"symbol":"ZZZ","direction":"long","confidence":0.9,"rationale":"ignored"}]}
```'''
views, regime = parse_second_opinion(content, syms)
assert regime == "USD strength"
assert views["EURUSD"] == (-1, 0.45, "x")
assert views["AUDUSD"][0] == -1 and views["AUDUSD"][1] == 1.0   # clamped to 1.0
assert "ZZZ" not in views                                       # not requested
assert "USDJPY" not in views                                    # omitted by model
print("parser OK")

if os.getenv("NVIDIA_API_KEY"):
    import bot  # noqa: F401  -- .env load
    from bot.client import MT5Client
    from bot.ai_signal import build_context
    from bot.config import CONFIG
    from bot.second_opinion import get_second_opinion

    c = MT5Client(magic=CONFIG.magic)
    c.connect()
    ctx = build_context(c, CONFIG.symbols)
    v, reg, usage = get_second_opinion(ctx, CONFIG.symbols, CONFIG.nemotron_model)
    print(f"regime: {reg}")
    print(f"rated {len(v)} symbols | tokens in={usage['in']} out={usage['out']}")
    assert isinstance(v, dict)
    c.shutdown()
    print("network smoke OK")
else:
    print("(NVIDIA_API_KEY unset — skipped network smoke)")
