"""How much M15 history can we pull per symbol from the terminal?"""
from datetime import datetime, timedelta
import MetaTrader5 as mt5
from bot.config import ALL_SYMBOLS

mt5.initialize()
for s in ALL_SYMBOLS:
    mt5.symbol_select(s, True)

tf = mt5.TIMEFRAME_M15
# try to grab ~45 days of M15 (covers the data window + live period)
for s in ALL_SYMBOLS:
    rates = mt5.copy_rates_from_pos(s, tf, 0, 5000)
    if rates is None or len(rates) == 0:
        print(f"  {s:7s} NO DATA  err={mt5.last_error()}")
        continue
    first = datetime.utcfromtimestamp(rates[0]['time'])
    last = datetime.utcfromtimestamp(rates[-1]['time'])
    span_days = (last - first).total_seconds() / 86400
    print(f"  {s:7s} bars={len(rates):5d}  {first:%Y-%m-%d %H:%M} -> {last:%Y-%m-%d %H:%M}  ({span_days:.1f}d)")
mt5.shutdown()
