"""Smoke test: connect, confirm algo trading, read account/risk/state.
Read-only — sends NO orders."""
from bot.client import MT5Client
from bot.risk import RiskEngine
from bot.state import StateTracker
from bot.config import CONFIG, INITIAL_EQUITY, ALL_SYMBOLS

c = MT5Client(magic=CONFIG.magic, deviation=CONFIG.order_deviation_points)
c.connect()
print("algo_allowed:", c.algo_allowed())

a = c.account()
print(f"acct {a.login} eq={a.equity:.2f} margin={a.margin:.2f} "
      f"level={a.margin_level:.1f}% lev={a.leverage}")

print("--- live quotes (all 15) ---")
for s in ALL_SYMBOLS:
    t = c.tick(s)
    sp = c.spread_points(s)
    print(f"  {s:7s} bid={getattr(t,'bid',None)} ask={getattr(t,'ask',None)} "
          f"spread_pts={sp}")

risk = RiskEngine(c, CONFIG.risk)
snap = risk.assess()
print("--- risk ---")
print(f"  gross_lev={snap.gross_leverage:.2f}x margin_usage={snap.margin_usage*100:.1f}% "
      f"allow_new={snap.allow_new_risk} kill={snap.kill}")
print("  reasons:", snap.reasons)

st = StateTracker(INITIAL_EQUITY, state_dir=CONFIG.state_dir)
m = st.update(a.equity)
print("--- state ---")
print(f"  return={m.return_pct*100:.3f}% maxDD={m.max_drawdown*100:.2f}% "
      f"sharpe={m.sharpe_15m:.3f} samples={m.n_samples}")

# sizing sanity check: how many lots for $50k notional on a few symbols
print("--- sizing ($50k notional) ---")
for s in ["EURUSD", "XAUUSD", "BTCUSD", "USDJPY"]:
    lots = c.lots_for_notional(s, 50_000)
    back = c.notional(s, lots)
    print(f"  {s:7s} -> {lots} lots ~= ${back:,.0f}")

c.shutdown()
print("OK")
