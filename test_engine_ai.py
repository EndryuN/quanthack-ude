"""Validate the production engine AI path end-to-end in DRY-RUN: real AI refresh
-> second opinion -> consensus -> sizer -> risk -> reconcile -> decision log."""
import json
import os
import time

import bot  # noqa: F401  (.env load)
from bot.engine import Engine

e = Engine(dry_run=True)          # signal_source defaults to "ai"
e.client.connect()
print(f"algo_allowed={e.client.algo_allowed()} signal={e.cfg.signal_source} "
      f"second_opinion={'off' if e._second_opinion_off else 'on'}")
e.tick()                          # launches the NON-BLOCKING AI refresh (daemon worker)
for _ in range(300):              # wait for the worker to finish (LLM calls)
    if e._refresh_thread is None or not e._refresh_thread.is_alive():
        break
    time.sleep(1)
e.tick()                          # applies the refresh -> sizes, dry-run reconciles, logs
e.client.shutdown()

day = time.strftime("%Y-%m-%d")
jpath = os.path.join(e.cfg.log_dir, f"ai_decisions_{day}.jsonl")
assert os.path.exists(jpath), f"expected decision log at {jpath}"
last = [json.loads(l) for l in open(jpath, encoding="utf-8")][-1]
assert "views" in last and last["views"], "record has views"
assert "account" in last and "cost" in last
print(f"\nOK — AI path validated, decision record written to {jpath}")
