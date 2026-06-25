"""The engine must never target a symbol ceded to the scalper, even if it is
still in the (stale) AI views — otherwise it re-opens it and the scalper stands
down forever on 'held by another system'. Pure, no MT5."""
import bot.engine as eng
from bot.engine import Engine


class A:
    equity = 1_000_000.0


e = Engine(dry_run=True)
e.cfg.signal_source = "ai"; e._controls = None
e._views = {"EURUSD": (1, 0.5), "GBPUSD": (-1, 0.5)}
e._active_symbols = ["GBPUSD"]            # EURUSD ceded to the scalper

orig = eng.build_targets
eng.build_targets = lambda client, views, equity, agg, cfg: dict(views)  # echo received views
try:
    out = e._build_targets(A())
finally:
    eng.build_targets = orig

assert "EURUSD" not in out, "ceded symbol must NOT be targeted by the engine"
assert "GBPUSD" in out, "active symbol is still targeted"

# the flat-fallback path is already active-only
e._views = None
assert set(e._build_targets(A()).keys()) == {"GBPUSD"}

print("OK")
