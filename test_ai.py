"""Live AI signal test: real Claude call -> views -> controlled sizing.
Sends NO orders. Requires ANTHROPIC_API_KEY in .env (or environment)."""
import os
import bot  # noqa: F401  -- triggers .env load
from bot.client import MT5Client
from bot.ai_signal import get_ai_views, build_context
from bot.sizer import build_targets, SizerConfig
from bot.config import CONFIG, ALL_SYMBOLS

if not os.getenv("ANTHROPIC_API_KEY"):
    raise SystemExit("ANTHROPIC_API_KEY not found. Put it in a .env file "
                     "(see .env.example) and re-run.")

c = MT5Client(magic=CONFIG.magic)
c.connect()
acct = c.account()

print("=== MARKET CONTEXT SENT TO CLAUDE ===")
print(build_context(c, ALL_SYMBOLS))

print("\n=== CALLING CLAUDE (claude-opus-4-8) ... ===")
views, mv, _usage = get_ai_views(c, ALL_SYMBOLS, effort=CONFIG.ai_effort)
print(f"\nREGIME: {mv.regime}\n")
for v in sorted(mv.views, key=lambda x: -x.confidence):
    print(f"  {v.symbol:7s} {v.direction:5s} conf={v.confidence:.2f}  {v.rationale}")

print(f"\n=== CONTROLLED SIZING (aggression={CONFIG.aggression}) ===")
tgt = build_targets(c, views, acct.equity, CONFIG.aggression, SizerConfig())
gross = sum(c.notional(s, abs(l)) for s, l in tgt.items() if l != 0)
for s, l in sorted(tgt.items(), key=lambda x: -c.notional(x[0], abs(x[1]))):
    if l != 0:
        print(f"  {s:7s} {('+' if l>0 else '')}{l:.2f} lots  ${c.notional(s,abs(l))/1e3:.0f}k")
used_margin_pct = (gross / 30) / acct.equity * 100
print(f"  gross ${gross/1e6:.2f}M = {gross/acct.equity:.1f}x leverage  "
      f"(used margin ~{used_margin_pct:.0f}% of equity)")
c.shutdown()
print("\nOK — no orders sent.")
