"""One-off connection probe: attach to the running MT5 terminal and dump
account + symbol state so we know exactly what we're working with."""
import MetaTrader5 as mt5

if not mt5.initialize():
    print("initialize() FAILED:", mt5.last_error())
    raise SystemExit(1)

print("=== TERMINAL ===")
ti = mt5.terminal_info()
print(f"  connected={ti.connected} trade_allowed={ti.trade_allowed} "
      f"build={ti.build} path={ti.path}")

print("=== ACCOUNT ===")
a = mt5.account_info()
print(f"  login={a.login} server={a.server} currency={a.currency}")
print(f"  balance={a.balance} equity={a.equity} margin={a.margin} "
      f"free={a.margin_free} margin_level={a.margin_level}")
print(f"  leverage={a.leverage} trade_allowed={a.trade_allowed} "
      f"margin_mode={a.margin_mode}  (0=netting,2=hedging)")

print("=== ALL SYMBOLS (count) ===")
syms = mt5.symbols_get()
print(f"  total symbols available: {len(syms)}")

# The 15 competition instruments — find their exact broker symbol names
wanted = ["AUDUSD", "EURCHF", "EURGBP", "EURUSD", "GBPUSD", "USDCAD",
          "USDCHF", "USDJPY", "XAGUSD", "XAUUSD", "BARUSD", "BTCUSD",
          "ETHUSD", "SOLUSD", "XRPUSD"]
print("=== COMPETITION INSTRUMENTS ===")
names = {s.name.upper(): s.name for s in syms}
for w in wanted:
    # match exact, or as a substring (brokers add suffixes like .r, m, etc.)
    hit = names.get(w) or next((s.name for s in syms if w in s.name.upper()), None)
    if hit:
        si = mt5.symbol_info(hit)
        tick = mt5.symbol_info_tick(hit)
        bid = tick.bid if tick else None
        print(f"  {w:7s} -> {hit:12s} vis={si.visible} "
              f"vol[{si.volume_min},{si.volume_max},step{si.volume_step}] "
              f"contract={si.trade_contract_size} digits={si.digits} bid={bid}")
    else:
        print(f"  {w:7s} -> NOT FOUND")

mt5.shutdown()
