import time
import MetaTrader5 as mt5
from bot.config import ALL_SYMBOLS

mt5.initialize()
for s in ALL_SYMBOLS:
    mt5.symbol_select(s, True)
print("selected all; waiting for ticks to stream...")
for attempt in range(6):
    time.sleep(2)
    missing = []
    for s in ALL_SYMBOLS:
        t = mt5.symbol_info_tick(s)
        if not t or t.bid == 0:
            missing.append(s)
    print(f"  t+{(attempt+1)*2}s missing quotes: {missing}")
    if not missing:
        break
print("--- final ---")
for s in ALL_SYMBOLS:
    t = mt5.symbol_info_tick(s)
    print(f"  {s:7s} bid={t.bid} ask={t.ask}")
mt5.shutdown()
