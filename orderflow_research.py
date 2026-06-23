"""Does order-book imbalance predict forward returns at TRADEABLE horizons?
Computes top-of-book imbalance from the L2 tick data, aggregates to a 15s grid,
and measures predictive power (information coefficient + conditional returns by
imbalance quintile) at 15s / 1m / 5m / 15m horizons.

If IC is meaningfully positive at minute+ horizons, order-flow is a real edge we
can trade at our loop cadence. If it only works at sub-second, it's HFT-only."""
import glob
import os
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from bot.dataloader import DATA_ROOT

SYMBOLS = ["EURUSD", "XAUUSD", "GBPUSD"]
DAYS = 4                      # sample first N available days per symbol
HORIZONS = {"15s": 1, "1m": 4, "5m": 20, "15m": 60}   # in 15s steps


def load_imbalance(path):
    tbl = pq.read_table(path, columns=["time", "bid", "ask",
                                       "bidsizes", "asksizes"])
    df = tbl.to_pandas()
    df["time"] = pd.to_datetime(df["time"])
    df = df.set_index("time")
    mid = (df["bid"] + df["ask"]) / 2.0
    # top-of-book sizes (first level)
    bsz = df["bidsizes"].str[0].astype(float)
    asz = df["asksizes"].str[0].astype(float)
    imb = (bsz - asz) / (bsz + asz)
    g = pd.DataFrame({"mid": mid, "imb": imb})
    # 15-second grid: mean imbalance, last mid
    r = g.resample("15s").agg({"imb": "mean", "mid": "last"}).dropna()
    return r


def analyze(sym):
    files = sorted(glob.glob(os.path.join(DATA_ROOT, f"{sym}_*.parquet")))[:DAYS]
    if not files:
        return
    parts = [load_imbalance(f) for f in files]
    r = pd.concat(parts).sort_index()
    r = r[~r.index.duplicated(keep="last")]
    mid = r["mid"].to_numpy()
    imb = r["imb"].to_numpy()
    print(f"\n=== {sym}  ({len(r)} 15s bars, {len(files)} days) ===")
    print(f"  imbalance mean={imb.mean():+.3f} std={imb.std():.3f}")
    for name, h in HORIZONS.items():
        if len(mid) <= h + 1:
            continue
        fwd = mid[h:] / mid[:-h] - 1.0
        x = imb[:-h]
        ok = np.isfinite(fwd) & np.isfinite(x)
        fwd, x = fwd[ok], x[ok]
        if len(x) < 50 or x.std() == 0:
            continue
        ic = np.corrcoef(x, fwd)[0, 1]
        # conditional mean fwd return for top vs bottom imbalance quintile
        q = np.quantile(x, [0.2, 0.8])
        hi = fwd[x >= q[1]].mean()
        lo = fwd[x <= q[0]].mean()
        spread_bp = (hi - lo) * 1e4
        print(f"  h={name:>3}: IC={ic:+.4f}  top-quintile fwd={hi*1e4:+.2f}bp  "
              f"bottom={lo*1e4:+.2f}bp  long-short={spread_bp:+.2f}bp")


if __name__ == "__main__":
    for s in SYMBOLS:
        analyze(s)
    print("\nIC = corr(imbalance, forward return). |IC|>~0.03 at minute horizons "
          "is a usable signal. Long-short = mean fwd return when imbalance is high "
          "minus when low (the tradeable spread, before costs).")
