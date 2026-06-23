"""APPROVED one-shot live execution test: open 0.01 BTCUSD, verify, close.
Minimal notional. Proves order_send, filling mode, and netting close work."""
import time
import MetaTrader5 as mt5
from bot.client import MT5Client
from bot.config import CONFIG

SYMBOL = "BTCUSD"
LOTS = 0.01

c = MT5Client(magic=CONFIG.magic, deviation=CONFIG.order_deviation_points)
c.connect()
assert c.algo_allowed(), "Algo Trading must be ON"

t = c.tick(SYMBOL)
print(f"{SYMBOL} bid={t.bid} ask={t.ask}  testing {LOTS} lots "
      f"(~${c.notional(SYMBOL, LOTS):,.0f} notional)")
print(f"chosen filling mode: {c._filling_mode(SYMBOL)}")

acct0 = c.account()
print(f"before: equity={acct0.equity:,.2f} positions={len(c.positions())}")

print("\n>>> OPENING 0.01 BUY...")
ok, res = c.market_order(SYMBOL, LOTS, is_buy=True, comment="exec-test")
print(f"  ok={ok} retcode={getattr(res,'retcode',res)} "
      f"comment={getattr(res,'comment','')} deal={getattr(res,'deal','')} "
      f"price={getattr(res,'price','')}")

time.sleep(1.5)
pos = c.position_for(SYMBOL)
print(f"  position now: {pos}")

print("\n>>> CLOSING...")
ok2, res2 = c.close_symbol(SYMBOL, comment="exec-test-close")
print(f"  ok={ok2} retcode={getattr(res2,'retcode',res2)} "
      f"comment={getattr(res2,'comment','')} price={getattr(res2,'price','')}")

time.sleep(1.5)
acct1 = c.account()
remaining = c.positions()
print(f"\nafter: equity={acct1.equity:,.2f} positions={len(remaining)} "
      f"(should be 0)  pnl_of_test={acct1.equity-acct0.equity:+.2f}")
print("RESULT:", "PASS — execution path works" if (ok and ok2 and not remaining)
      else "CHECK — see retcodes above")
c.shutdown()
