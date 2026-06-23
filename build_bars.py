import time
from bot.dataloader import build_cache, BACKTEST_SYMBOLS
t0 = time.time()
print(f"building M15 cache for {len(BACKTEST_SYMBOLS)} symbols...")
build_cache()
print(f"\ndone in {(time.time()-t0)/60:.1f} min")
