"""A stale control_ack.json (ahead of controls.json's *_seq) silently swallows
operator actions — 'Apply preset' triggers no reconcile. The engine re-syncs the
ack to controls on launch so the next bump is always seen. No MT5."""
import json, os, tempfile
from bot.engine import Engine
from bot.controls import load_ack


d = tempfile.mkdtemp()
# controls.json: counters reset low (fresh file / churn)
json.dump({"apply_seq": 2, "flatten_seq": 0, "refresh_seq": 1},
          open(os.path.join(d, "controls.json"), "w"))
# control_ack.json: STALE, ahead from a previous session -> would ignore apply/flatten
json.dump({"apply_seq_done": 9, "flatten_seq_done": 7, "refresh_seq_done": 9},
          open(os.path.join(d, "control_ack.json"), "w"))

e = Engine(dry_run=True); e.cfg.use_control_panel = True; e.cfg.state_dir = d
e._sync_control_ack()

ack = load_ack(d)
assert ack["apply_seq_done"] == 2, ack          # synced down to controls
assert ack["flatten_seq_done"] == 0 and ack["refresh_seq_done"] == 1, ack

# now the NEXT operator apply (controls 2 -> 3) is strictly greater than the ack,
# so the handler fires (apply_seq > apply_seq_done)
assert 3 > ack["apply_seq_done"], "next apply bump must be detectable"
# and no phantom flatten: controls flatten_seq (0) is NOT > ack flatten_seq_done (0)
assert not (0 > ack["flatten_seq_done"]), "no phantom flatten on launch"

print("OK")
