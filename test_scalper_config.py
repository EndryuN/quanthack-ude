"""ScalperConfig exists; symbol-exclusivity is conditional on enabled."""
import bot.config as cfg

# defaults: disabled, EURUSD default scalp symbol, magic 990624
assert cfg.SCALPER.enabled is False
assert "BTCUSD" in cfg.SCALPER.symbols   # Phase 2 default slot symbol
assert cfg.SCALPER.magic == 990624 and cfg.SCALPER.magic != cfg.CONFIG.magic

# scalper OFF (default) -> engine universe is UNCHANGED (still trades EURUSD)
assert "EURUSD" in cfg.CONFIG.symbols, "engine must be unchanged while scalper off"

# the helper that builds the engine universe cedes scalper symbols when enabled
on = cfg.engine_symbols(scalper_enabled=True, scalper_symbols=["EURUSD"])
off = cfg.engine_symbols(scalper_enabled=False, scalper_symbols=["EURUSD"])
assert "EURUSD" not in on and "EURUSD" in off
assert "BARUSD" not in on and "BARUSD" not in off   # BARUSD always excluded
print("OK")
