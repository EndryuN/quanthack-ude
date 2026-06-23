"""Pure pricing helper: Claude Opus $5/M in, $25/M out."""
from bot.ai_signal import cost_usd

assert cost_usd(0, 0) == 0.0
assert abs(cost_usd(1_000_000, 0) - 5.0) < 1e-9
assert abs(cost_usd(0, 1_000_000) - 25.0) < 1e-9
assert abs(cost_usd(2494, 1357) - (2494 * 5 + 1357 * 25) / 1e6) < 1e-12
print("OK")
