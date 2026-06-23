"""Run the engine for a few dry-run ticks to validate the full pipeline:
bars -> signals -> sizing -> reconcile (logged, not sent)."""
from bot.engine import Engine
from bot.strategy import HybridStrategy

e = Engine(dry_run=True)
e.client.connect()
print("algo_allowed:", e.client.algo_allowed())

# show raw signals once so we can see what the strategy sees
acct = e.client.account()
sigs = e.strategy.signals(e.client, e.cfg.symbols)
print("\n--- SIGNALS ---")
for s, sg in sigs.items():
    print(f"  {s:7s} dir={sg.direction:+d} conviction={sg.conviction:.3f} vol={sg.vol:.5f}")

tgt = e.strategy.target_positions(e.client, e.cfg.symbols, acct.equity)
print("\n--- TARGET POSITIONS (signed lots) ---")
gross = 0.0
for s, lots in tgt.items():
    if lots != 0:
        notional = e.client.notional(s, abs(lots))
        gross += notional
        print(f"  {s:7s} {lots:+.2f} lots  ~${notional:,.0f}")
print(f"  gross notional ~${gross:,.0f}  => {gross/acct.equity:.2f}x leverage")

print("\n--- TICK (dry-run reconcile) ---")
e.tick()
e.client.shutdown()
print("\nOK")
