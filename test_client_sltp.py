"""Position carries sl/tp; set_sltp exists and sets both legs. The live broker
call is a gated smoke (needs MT5 + an open position)."""
import os
from bot.client import Position, MT5Client

# dataclass carries sl/tp with defaults
p = Position(ticket=1, symbol="EURUSD", volume=1.0, type=0, price_open=1.0,
             price_current=1.0, profit=0.0)
assert p.sl == 0.0 and p.tp == 0.0, "Position has sl/tp defaulting to 0.0"
p2 = Position(ticket=1, symbol="EURUSD", volume=1.0, type=0, price_open=1.0,
              price_current=1.0, profit=0.0, sl=1.09, tp=1.11)
assert p2.sl == 1.09 and p2.tp == 1.11

# set_sltp is present and callable (no-position path returns cleanly, needs MT5)
assert hasattr(MT5Client, "set_sltp")
if os.getenv("PANEL_LIVE_SLTP"):  # opt-in live smoke only
    import bot  # noqa: F401
    c = MT5Client(); c.connect()
    ok, res = c.set_sltp("EURUSD", 0.0, 0.0)   # no-op SLTP; safe
    print("live set_sltp:", ok, res)
    c.shutdown()
print("OK")
