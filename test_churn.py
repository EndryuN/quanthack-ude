"""Prove the churn fix: run 4 ticks back-to-back. Tick 1 refreshes views and
reconciles once; ticks 2-4 must produce ZERO orders (signal unchanged)."""
import bot  # noqa: F401
from bot.engine import Engine

e = Engine(dry_run=True)          # ai mode by default
e.client.connect()
for i in range(1, 5):
    print(f"\n----- TICK {i} -----")
    before = e._last_acted_ts
    e.tick()
    reconciled = e._last_acted_ts != before
    print(f"(reconciled this tick: {reconciled})")
e.client.shutdown()
print("\nEXPECTED: reconcile only on tick 1; ticks 2-4 reconciled=False, orders=0")
