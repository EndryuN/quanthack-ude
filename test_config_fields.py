"""Config carries the new logging + second-opinion knobs with sane defaults."""
from bot.config import CONFIG

assert CONFIG.log_decisions is True
assert CONFIG.log_dir == "logs"
assert CONFIG.use_second_opinion is True
assert CONFIG.nemotron_model.startswith("nvidia/")
assert 0.0 <= CONFIG.nemotron_consensus_gain <= 1.0
assert CONFIG.second_opinion_timeout > 0
print("OK")
