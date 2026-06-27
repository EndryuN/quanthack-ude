"""Realized P&L by instrument — which symbols made (or lost) the most.

Run on the laptop (needs the MT5 terminal up):
    python pnl_by_symbol.py            # all history
    python pnl_by_symbol.py 3          # last 3 days

Sums CLOSED-deal P&L (profit + swap + commission) per symbol for our own magics
(engine + scalper), sorted best -> worst. Open-position floating P&L is NOT included
(see the panel's Positions card for that)."""
import sys
import datetime as dt
from collections import defaultdict

import MetaTrader5 as mt5
from bot.client import MT5Client
from bot.config import CONFIG, SCALPER

days = float(sys.argv[1]) if len(sys.argv) > 1 else None
OURS = {CONFIG.magic, SCALPER.magic}

c = MT5Client(magic=CONFIG.magic)
c.connect()

frm = (dt.datetime.now() - dt.timedelta(days=days)) if days else dt.datetime(2000, 1, 1)
to = dt.datetime.now() + dt.timedelta(days=1)
deals = mt5.history_deals_get(frm, to) or ()

agg = defaultdict(lambda: {"pnl": 0.0, "trades": 0, "wins": 0})
for d in deals:
    if getattr(d, "magic", None) not in OURS:
        continue
    a = agg[getattr(d, "symbol", "") or "?"]
    a["pnl"] += getattr(d, "profit", 0.0) + getattr(d, "swap", 0.0) + getattr(d, "commission", 0.0)
    if getattr(d, "entry", None) == mt5.DEAL_ENTRY_OUT:      # a closing deal = one round-trip
        a["trades"] += 1
        if getattr(d, "profit", 0.0) > 0:
            a["wins"] += 1

rows = sorted(agg.items(), key=lambda kv: kv[1]["pnl"], reverse=True)
total = sum(a["pnl"] for _, a in rows)

span = "all history" if not days else f"last {days:g}d"
print(f"\nRealized P&L by instrument ({span}) — magics {sorted(OURS)}\n")
print(f"{'symbol':<10}{'net P&L':>14}{'trades':>8}{'win%':>7}")
print("-" * 39)
for sym, a in rows:
    wr = (a["wins"] / a["trades"] * 100) if a["trades"] else 0.0
    print(f"{sym:<10}{a['pnl']:>14,.2f}{a['trades']:>8}{wr:>6.0f}%")
print("-" * 39)
print(f"{'TOTAL':<10}{total:>14,.2f}")

c.shutdown()
