"""Output formatting: retcode helper is silent on success, named on failure;
close line shows profit + reason. No MT5."""
from bot.engine import _rc, Engine


class _Res:
    def __init__(self, rc): self.retcode = rc

# success (10009) -> empty string (no noise)
assert _rc(True, _Res(10009)) == ""
# failure -> shows code + a human name when known
out = _rc(False, _Res(10019))
assert "FAILED" in out and "10019" in out and "money" in out.lower()
# unknown failure code still shows the number, no name crash
out2 = _rc(False, _Res(99999))
assert "FAILED" in out2 and "99999" in out2

# _close_reason: AI view flat -> "flat"; flip -> "flipped"; else de-risked
e = Engine(dry_run=True)
e._views = {"EURUSD": (0, 0.0), "USDJPY": (1, 0.6)}
e._pending_log = {"views": [{"symbol": "EURUSD", "claude": {"rationale": "range-bound, no edge"}}]}
r_flat = e._close_reason("EURUSD", cur=2.0)    # held long, AI now flat
assert "flat" in r_flat.lower() and "range-bound" in r_flat
r_flip = e._close_reason("USDJPY", cur=-1.0)   # held short, AI now long -> flip
assert "flip" in r_flip.lower()
print("OK")
