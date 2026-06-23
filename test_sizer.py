"""Validate the variance-aware sizer + AI-path engine plumbing using FAKE views
(no API key needed). Confirms aggression dial behaves and engine reconciles."""
from bot.client import MT5Client
from bot.sizer import build_targets, SizerConfig
from bot.config import CONFIG, ALL_SYMBOLS
from bot.engine import Engine

c = MT5Client(magic=CONFIG.magic)
c.connect()
acct = c.account()

# fake "AI" views: a USD-strength story (short risk/commodity FX, long USD vs JPY)
fake = {
    "EURUSD": (-1, 0.45), "GBPUSD": (-1, 0.40), "AUDUSD": (-1, 0.65),
    "USDJPY": (1, 0.55), "USDCAD": (1, 0.30), "USDCHF": (1, 0.25),
    "EURCHF": (0, 0.0), "EURGBP": (0, 0.0),
    "XAUUSD": (-1, 0.70), "XAGUSD": (-1, 0.50),
    "BTCUSD": (-1, 0.80), "ETHUSD": (-1, 0.60), "SOLUSD": (-1, 0.55),
    "XRPUSD": (-1, 0.30), "BARUSD": (0, 0.0),
}

cfg = SizerConfig()
print(f"equity={acct.equity:,.0f}\n")
for a in [0.0, 0.3, 0.7, 1.0]:
    tgt = build_targets(c, fake, acct.equity, a, cfg)
    gross = sum(c.notional(s, abs(l)) for s, l in tgt.items() if l != 0)
    nonzero = {s: l for s, l in tgt.items() if l != 0}
    top = sorted(nonzero.items(), key=lambda x: -c.notional(x[0], abs(x[1])))[:4]
    topstr = ", ".join(f"{s}{('+' if l>0 else '')}{l:.1f}(${c.notional(s,abs(l))/1e3:.0f}k)"
                       for s, l in top)
    print(f"aggression={a:.1f}: gross=${gross/1e6:.2f}M ({gross/acct.equity:.1f}x) "
          f"names={len(nonzero)} | top: {topstr}")

print("\n--- engine AI-path dry-run tick (injected views) ---")
e = Engine(dry_run=True)
e.client = c
e.cfg.signal_source = "ai"
e.cfg.aggression = 0.3
e._views = fake
e._views_ts = 9e18    # pretend fresh so it won't call the API
e.tick()
c.shutdown()
print("\nOK")
