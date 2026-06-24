"""CLI: backtest the scalp on local FX tick data (or live MT5 M1 for crypto) and
sweep the entry/tp/stop grid. Same engine the panel's Scalper page calls.
Usage: python run_scalp_backtest.py [SYMBOL]   (default EURUSD)"""
import sys

from bot.scalp_backtest import sweep_scalp

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "EURUSD"


def main():
    res = sweep_scalp(SYMBOL)   # local parquet, else live MT5 M1
    if not res.get("available"):
        print(f"no M1 data for {SYMBOL} from local parquet OR MT5 "
              f"(see notes above). For crypto, make sure the MT5 terminal is "
              f"open and {SYMBOL} is available from the broker.")
        return
    print(f"{SYMBOL}: {res['bars']} M1 bars, cost_frac={res['cost_bps']:.2f}bps/round-trip")
    print(f"{'entry':>6}{'tp':>6}{'stop':>6}{'trades':>8}{'win%':>7}{'net_bps':>9}{'sharpe':>8}{'maxDD':>8}")
    for r in res["rows"]:
        print(f"{r['entry']:6.0f}{r['tp']:6.0f}{r['stop']:6.0f}{r['trades']:8d}"
              f"{r['win_rate']*100:7.1f}{r['net_pnl_bps']:9.1f}"
              f"{r['sharpe']:8.3f}{r['max_dd_bps']:8.1f}")
    best = res["best"]
    if best:
        print(f"\nBEST by Sharpe (>=30 trades): entry={best['entry']} tp={best['tp']} "
              f"stop={best['stop']} -> net={best['net_pnl_bps']}bps "
              f"sharpe={best['sharpe']} win={best['win_rate']*100:.1f}%")
        print("GATE: arm live only if net_pnl_bps > 0 after cost AND sharpe clears the AI engine's.")
    else:
        print("\nno parameter set produced >=30 trades -- no edge to arm.")


if __name__ == "__main__":
    main()
