"""Phase-2 crypto momentum signal (pure). Breakout entries, BTC lead-lag for
alts, ride-or-flip exits."""
from bot.crypto_signal import crypto_decision, CryptoParams

P = CryptoParams(lookback=3, entry_bps=20.0, lead_lookback=2, lead_bps=10.0)

# up-breakout from flat -> long (BTC itself, no lead needed)
up = [100.0, 100.0, 100.0, 100.5]            # +50 bps over 3 bars >= 20
d, _ = crypto_decision(up, 0, p=P)
assert d == 1, d
# down-breakout -> short
down = [100.0, 100.0, 100.0, 99.5]
d, _ = crypto_decision(down, 0, p=P)
assert d == -1, d
# no breakout -> stay flat
flat = [100.0, 100.0, 100.0, 100.05]         # +5 bps < 20
d, _ = crypto_decision(flat, 0, p=P)
assert d == 0, d

# in a long, momentum continues -> HOLD (broker TP/SL handles exit)
d, r = crypto_decision(up, 1, p=P)
assert d == 1 and r == "hold"
# in a long, REVERSE breakout -> flip short
d, _ = crypto_decision(down, 1, p=P)
assert d == -1
# in a long, momentum fades (no breakout) -> hold (no mean-revert exit)
d, _ = crypto_decision(flat, 1, p=P)
assert d == 1

# ALT lead-lag: up-breakout CONFIRMED by BTC up -> long
btc_up = [100.0, 100.0, 100.2]               # +20 bps over 2 >= 10
d, r = crypto_decision(up, 0, btc_closes=btc_up, p=P)
assert d == 1 and "+BTC" in r
# ALT up-breakout but BTC DOWN -> unconfirmed, stand down (stay flat)
btc_down = [100.0, 100.0, 99.8]
d, r = crypto_decision(up, 0, btc_closes=btc_down, p=P)
assert d == 0 and "unconfirmed" in r
# ALT with BTC flat -> unconfirmed
btc_flat = [100.0, 100.0, 100.02]
d, _ = crypto_decision(up, 0, btc_closes=btc_flat, p=P)
assert d == 0

print("OK")
