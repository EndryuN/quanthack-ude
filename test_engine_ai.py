"""Validate the production engine AI path end-to-end in DRY-RUN: real AI refresh
-> sizer -> risk -> reconcile (logged, no orders)."""
import bot  # noqa: F401  (.env load)
from bot.engine import Engine

e = Engine(dry_run=True)          # signal_source defaults to "ai"
e.start_once = True
e.client.connect()
print(f"algo_allowed={e.client.algo_allowed()} signal={e.cfg.signal_source} "
      f"aggression={e.cfg.aggression} broker_sl={e.cfg.use_broker_sl}")
e.tick()                          # refreshes AI views, sizes, dry-run reconciles
e.client.shutdown()
print("\nOK — production AI path validated, no orders sent.")
