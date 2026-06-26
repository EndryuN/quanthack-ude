"""Manual probe of a LARGER Nemotron variant against the engine's real blind
second-opinion path. Compares the current model with nvidia/nemotron-3-ultra-550b-a55b
so you can see if the bigger model is a viable drop-in (does it return the JSON
schema, how slow, what does it say).

The API key is read from NVIDIA_API_KEY (loaded from .env if present) — it is NOT
hardcoded here. Run on a machine with the key configured:

    python test_nemotron_ultra.py
"""
import os, time

try:
    from dotenv import load_dotenv; load_dotenv()
except Exception:
    pass

from bot.second_opinion import get_second_opinion
from bot.config import CONFIG

# the model under test vs the one in production
ULTRA = "nvidia/nemotron-3-ultra-550b-a55b"
SUPER = CONFIG.nemotron_model            # nvidia/nemotron-3-super-120b-a12b

SYMBOLS = ["EURUSD", "GBPUSD", "XAUUSD", "BTCUSD"]
# a realistic build_context() snapshot so the model has something to reason about
CONTEXT = (
    "EURUSD: px=1.08423 | ret 1h=-0.04 4h=+0.11 1d=-0.23 3d=+0.87% | trend M15=up H1=up H4=dn | mom_z=+0.41 vol/bar=0.06% | 5d-range=61%\n"
    "GBPUSD: px=1.27310 | ret 1h=+0.02 4h=-0.08 1d=+0.15 3d=-0.30% | trend M15=dn H1=up H4=up | mom_z=-0.12 vol/bar=0.07% | 5d-range=44%\n"
    "XAUUSD: px=2335.20 | ret 1h=+0.10 4h=+0.35 1d=+1.10 3d=+2.40% | trend M15=up H1=up H4=up | mom_z=+1.30 vol/bar=0.22% | 5d-range=88%\n"
    "BTCUSD: px=61250 | ret 1h=-0.50 4h=-1.20 1d=-3.40 3d=-6.10% | trend M15=dn H1=dn H4=dn | mom_z=-1.80 vol/bar=0.55% | 5d-range=12%"
)


def probe(model: str, timeout: float):
    print(f"\n=== {model} (timeout {timeout:.0f}s) ===")
    t0 = time.time()
    try:
        views, regime, usage = get_second_opinion(CONTEXT, SYMBOLS, model, timeout=timeout)
    except Exception as e:
        print(f"  FAILED in {time.time()-t0:.1f}s: {type(e).__name__}: {e}")
        return False
    dt = time.time() - t0
    print(f"  OK in {dt:.1f}s | tokens in/out = {usage['in']}/{usage['out']}")
    print(f"  regime: {regime}")
    for s in SYMBOLS:
        print(f"    {s}: {views.get(s)}")
    return True


if __name__ == "__main__":
    if not os.getenv("NVIDIA_API_KEY"):
        raise SystemExit("NVIDIA_API_KEY not set (put it in .env). Not hardcoding the key.")
    # the ultra model is a bigger reasoning model -> give it a generous timeout
    probe(SUPER, timeout=60.0)
    probe(ULTRA, timeout=240.0)
    print("\nOK")
