"""Backtest the hybrid strategy on MT5 terminal history (recent ~12d validation
slice). Also runs a small parameter sweep over leverage and entry threshold."""
import numpy as np
import MetaTrader5 as mt5

from bot.backtest import SymbolMeta, run_backtest
from bot.strategy import StrategyConfig
from bot.config import ALL_SYMBOLS, RiskLimits

mt5.initialize()
for s in ALL_SYMBOLS:
    mt5.symbol_select(s, True)

TF = mt5.TIMEFRAME_M15
data, meta = {}, {}
for s in ALL_SYMBOLS:
    rates = mt5.copy_rates_from_pos(s, TF, 0, 2000)
    if rates is None or len(rates) < 100:
        print(f"  skip {s}: insufficient data")
        continue
    data[s] = rates
    info = mt5.symbol_info(s)
    tick = mt5.symbol_info_tick(s)
    price = (tick.bid + tick.ask) / 2 if tick else 1.0
    spread = (tick.ask - tick.bid) if tick else 0.0
    cost_frac = (spread / price) if price > 0 else 0.0001
    meta[s] = SymbolMeta(
        contract_size=info.trade_contract_size,
        volume_min=info.volume_min, volume_max=info.volume_max,
        volume_step=info.volume_step,
        usd_base=(s.startswith("USD") and not s.endswith("USD")),
        cost_frac=max(cost_frac, 1e-5),
    )
mt5.shutdown()

syms = list(data.keys())
print(f"loaded {len(syms)} symbols; bars: {min(len(data[s]) for s in syms)}-{max(len(data[s]) for s in syms)}")
print(f"cost_frac sample: " + ", ".join(f"{s}={meta[s].cost_frac*1e4:.1f}bp" for s in ["EURUSD","BTCUSD","XAUUSD"]))

print("\n=== BASELINE (default config) ===")
base = run_backtest(data, meta, verbose=True)

print("\n=== SWEEP: core/max leverage x entry_z ===")
print(f"{'core':>5}{'max':>5}{'entry_z':>9}{'ret%':>9}{'dd%':>8}{'sharpe':>8}{'trades':>8}{'blew':>6}")
results = []
for core, mx in [(3, 5), (4, 7), (6, 10), (8, 12)]:
    for ez in [0.08, 0.12, 0.20]:
        scfg = StrategyConfig(core_leverage=core, max_leverage=mx, entry_z=ez)
        r = run_backtest(data, meta, scfg=scfg)
        results.append((core, mx, ez, r))
        print(f"{core:>5}{mx:>5}{ez:>9.2f}{r.ret*100:>9.2f}{r.max_dd*100:>8.2f}"
              f"{r.sharpe:>8.3f}{r.n_trades:>8}{str(r.blew_up):>6}")

# best by a simple proxy: return rank dominates but penalize blowups/big DD
viable = [(c, m, e, r) for (c, m, e, r) in results if not r.blew_up]
if viable:
    best = max(viable, key=lambda x: x[3].ret - 0.5 * x[3].max_dd)
    c, m, e, r = best
    print(f"\nbest (ret - 0.5*dd, no blowup): core={c} max={m} entry_z={e} "
          f"-> ret={r.ret*100:+.2f}% dd={r.max_dd*100:.2f}% sharpe={r.sharpe:.3f}")
