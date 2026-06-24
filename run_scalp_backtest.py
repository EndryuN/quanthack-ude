"""CLI: backtest the scalp on local FX tick data + sweep the entry/tp/stop grid.
Usage: python run_scalp_backtest.py [SYMBOL]   (default EURUSD)"""
import sys

from bot.scalp_signal import ScalpParams
from bot.scalp_backtest import m1_bars, backtest_scalp

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "EURUSD"


def main():
    closes, cost_frac = m1_bars(SYMBOL)   # local parquet, else live MT5 M1
    if not closes:
        print(f"no M1 data for {SYMBOL} from local parquet OR MT5 "
              f"(see notes above). For crypto, make sure the MT5 terminal is "
              f"open and {SYMBOL} is available from the broker.")
        return
    print(f"{SYMBOL}: {len(closes)} M1 bars, cost_frac={cost_frac*1e4:.2f}bps/round-trip")
    print(f"{'entry':>6}{'tp':>6}{'stop':>6}{'trades':>8}{'win%':>7}{'net_bps':>9}{'sharpe':>8}{'maxDD':>8}")
    best = None
    for entry in (3.0, 5.0, 8.0):
        for tp in (3.0, 5.0, 8.0):
            for stop in (8.0, 12.0):
                p = ScalpParams(ema_period=20, entry_bps=entry, exit_bps=1.0,
                                tp_bps=tp, stop_bps=stop, time_stop_bars=10)
                r = backtest_scalp(closes, p, cost_frac)
                print(f"{entry:6.0f}{tp:6.0f}{stop:6.0f}{r['trades']:8d}"
                      f"{r['win_rate']*100:7.1f}{r['net_pnl_bps']:9.1f}"
                      f"{r['sharpe']:8.3f}{r['max_dd_bps']:8.1f}")
                if r["trades"] >= 30 and (best is None or r["sharpe"] > best[1]["sharpe"]):
                    best = ((entry, tp, stop), r)
    if best:
        (e, tp, st), r = best
        print(f"\nBEST by Sharpe (>=30 trades): entry={e} tp={tp} stop={st} -> "
              f"net={r['net_pnl_bps']}bps sharpe={r['sharpe']} win={r['win_rate']*100:.1f}%")
        print("GATE: arm live only if net_pnl_bps > 0 after cost AND sharpe clears the AI engine's.")
    else:
        print("\nno parameter set produced >=30 trades -- no edge to arm.")


if __name__ == "__main__":
    main()
