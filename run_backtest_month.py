"""Full-month backtest on the cached L2-derived M15 bars (FX + metals, the 10
instruments present in the data). Train/test split to avoid overfitting, and a
sweep over mode x leverage x entry_z x cadence."""
import numpy as np

from bot.dataloader import load_bars, BACKTEST_SYMBOLS
from bot.backtest import run_backtest
from bot.strategy import StrategyConfig

SPLIT_EPOCH = int(np.datetime64("2026-06-01T00:00:00").astype("datetime64[s]").astype(int))


def slice_data(data, lo=None, hi=None):
    out = {}
    for s, rec in data.items():
        m = np.ones(len(rec), dtype=bool)
        if lo is not None:
            m &= rec["time"] >= lo
        if hi is not None:
            m &= rec["time"] < hi
        if m.sum() > 100:
            out[s] = rec[m]
    return out


def main():
    data, meta = load_bars()
    if not data:
        print("no cached bars found — run build_bars.py first")
        return
    syms = list(data.keys())
    span = (min(rec["time"][0] for rec in data.values()),
            max(rec["time"][-1] for rec in data.values()))
    print(f"loaded {len(syms)} symbols: {syms}")
    print(f"bars/symbol: {min(len(d) for d in data.values())}-{max(len(d) for d in data.values())}")
    print(f"period: {np.datetime64(int(span[0]),'s')} -> {np.datetime64(int(span[1]),'s')}")

    train = slice_data(data, hi=SPLIT_EPOCH)
    test = slice_data(data, lo=SPLIT_EPOCH)
    print(f"train bars: {max(len(d) for d in train.values())}  "
          f"test bars: {max(len(d) for d in test.values())}\n")

    print("=== BASELINE momentum (full period, default cfg) ===")
    run_backtest(data, meta, verbose=True)
    print("=== BASELINE meanrev (full period) ===")
    run_backtest(data, meta, scfg=StrategyConfig(mode="meanrev"), verbose=True)

    print("\n=== SWEEP (train -> test) ===")
    hdr = f"{'mode':>8}{'core':>5}{'max':>5}{'ez':>6}{'dec':>5}|{'trainRet%':>10}{'trDD%':>7}{'trSh':>7}|{'testRet%':>10}{'teDD%':>7}{'teSh':>7}{'tr#':>7}"
    print(hdr)
    rows = []
    for mode in ["momentum", "meanrev"]:
        for core, mx in [(3, 5), (6, 10)]:
            for ez in [0.08, 0.15, 0.25]:
                for dec in [1, 4, 8]:
                    scfg = StrategyConfig(mode=mode, core_leverage=core,
                                          max_leverage=mx, entry_z=ez,
                                          decision_every=dec)
                    rtr = run_backtest(train, meta, scfg=scfg)
                    rte = run_backtest(test, meta, scfg=scfg)
                    rows.append((mode, core, mx, ez, dec, rtr, rte))
                    print(f"{mode:>8}{core:>5}{mx:>5}{ez:>6.2f}{dec:>5}|"
                          f"{rtr.ret*100:>10.2f}{rtr.max_dd*100:>7.2f}{rtr.sharpe:>7.2f}|"
                          f"{rte.ret*100:>10.2f}{rte.max_dd*100:>7.2f}{rte.sharpe:>7.2f}{rte.n_trades:>7}")

    # pick: positive on BOTH windows, no blowup, best test return
    good = [r for r in rows if not r[5].blew_up and not r[6].blew_up
            and r[5].ret > 0 and r[6].ret > 0]
    print(f"\nconfigs positive on BOTH train & test: {len(good)}/{len(rows)}")
    if good:
        best = max(good, key=lambda r: r[6].ret + 0.01 * r[6].sharpe)
        mode, core, mx, ez, dec, rtr, rte = best
        print(f"BEST robust: mode={mode} core={core} max={mx} entry_z={ez} dec={dec}")
        print(f"  train ret={rtr.ret*100:+.2f}% dd={rtr.max_dd*100:.2f}% sh={rtr.sharpe:.2f}")
        print(f"  test  ret={rte.ret*100:+.2f}% dd={rte.max_dd*100:.2f}% sh={rte.sharpe:.2f}")
    else:
        print("NONE robustly positive — strategy needs rethink (see notes).")


if __name__ == "__main__":
    main()
