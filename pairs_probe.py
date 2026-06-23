"""Probe for relative-value (stat-arb) edge: do any natural spreads mean-revert
tradably? Fit hedge ratio + spread z-score on TRAIN, backtest a market-neutral
z-reversion on TEST. Economic-basis pairs only."""
import numpy as np
from bot.dataloader import load_bars

CANDIDATES = [
    ("XAUUSD", "XAGUSD"),   # gold/silver ratio — classic mean-reverter
    ("EURUSD", "GBPUSD"),   # EUR vs GBP (both vs USD)
    ("EURUSD", "EURCHF"),
    ("EURUSD", "EURGBP"),
    ("USDCHF", "USDCAD"),
    ("AUDUSD", "EURUSD"),
    ("EURGBP", "GBPUSD"),
]

data, meta = load_bars()

# align all symbols on common timestamp grid
common = None
for s, rec in data.items():
    ts = set(rec["time"].tolist())
    common = ts if common is None else (common & ts)
grid = np.array(sorted(common))
close = {}
for s, rec in data.items():
    idx = {int(t): c for t, c in zip(rec["time"], rec["close"])}
    close[s] = np.array([idx[int(t)] for t in grid])
print(f"aligned {len(grid)} bars across {len(close)} symbols")

split = len(grid) // 2


def half_life(spread):
    s = spread - spread.mean()
    ds = np.diff(s)
    lag = s[:-1]
    b = np.polyfit(lag, ds, 1)[0]
    return (-np.log(2) / b) if b < 0 else np.inf


def probe(a, b):
    la, lb = np.log(close[a]), np.log(close[b])
    # hedge ratio from train
    beta = np.polyfit(lb[:split], la[:split], 1)[0]
    spread = la - beta * lb
    mu, sd = spread[:split].mean(), spread[:split].std()
    if sd == 0:
        return None
    z = (spread - mu) / sd
    hl = half_life(spread[:split])

    # market-neutral z-reversion on TEST: position = -clip(z) (fade), unit gross
    ztest = z[split:]
    ra = np.diff(la[split:])        # log returns leg a
    rb = np.diff(lb[split:])
    pos = -np.clip(ztest, -3, 3) / 3.0          # in [-1,1], short spread when z>0
    pos = pos[:-1]
    # spread return ~ ra - beta*rb (dollar-neutral-ish in log space)
    sret = ra - beta * rb
    pnl = pos * sret
    if pnl.std() == 0:
        return None
    sharpe = pnl.mean() / pnl.std()
    cum = np.sum(pnl)
    return beta, hl, sharpe, cum, np.corrcoef(la[:split], lb[:split])[0, 1]


print(f"\n{'pair':>16}{'beta':>8}{'half-life(bars)':>16}{'corr':>7}{'testSharpe':>12}{'testCumLogRet':>15}")
for a, b in CANDIDATES:
    if a not in close or b not in close:
        continue
    r = probe(a, b)
    if r is None:
        continue
    beta, hl, sharpe, cum, corr = r
    hl_s = f"{hl:.0f}" if np.isfinite(hl) else "inf"
    print(f"{a+'/'+b:>16}{beta:>8.3f}{hl_s:>16}{corr:>7.2f}{sharpe:>12.4f}{cum*100:>14.2f}%")
print("\n(testSharpe is per-bar, non-annualized — same units as the competition.)")
