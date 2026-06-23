import time, glob, os
from bot.dataloader import _bars_from_file, DATA_ROOT
f = sorted(glob.glob(os.path.join(DATA_ROOT, "EURUSD_*.parquet")))[0]
t0 = time.time()
b = _bars_from_file(f)
print(f"parsed {os.path.basename(f)} in {time.time()-t0:.1f}s -> {len(b)} M15 bars")
print(b.head())
print("avg spread (price):", b["spread"].mean())
