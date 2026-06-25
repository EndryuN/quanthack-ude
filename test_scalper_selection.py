"""Dynamic scalper selection foundation (spec §9.4): the scalper_symbols control,
the engine's live universe, and the partition invariant. Pure, no MT5."""
from bot.controls import scalper_assignments, DEFAULT_CONTROLS
from bot.config import live_engine_symbols, assert_symbol_partition, ALL_SYMBOLS

# defaults: nothing assigned, not armed
assert DEFAULT_CONTROLS["scalper_symbols"] == [] and DEFAULT_CONTROLS["scalper_armed"] == []

# assignments: filtered to valid instruments, de-duped, order-stable
assert scalper_assignments({"scalper_symbols": ["EURUSD", "EURUSD", "NOPE", "BTCUSD"]},
                           ALL_SYMBOLS) == ["EURUSD", "BTCUSD"]
assert scalper_assignments(None, ALL_SYMBOLS) == []

# live universe cedes assigned symbols (and always drops BARUSD)
uni = live_engine_symbols(["EURUSD", "BTCUSD"])
assert "EURUSD" not in uni and "BTCUSD" not in uni
assert "GBPUSD" in uni and "BARUSD" not in uni
# unassigning returns the symbol to the engine
assert "EURUSD" in live_engine_symbols([])

# partition invariant: disjoint + valid passes; overlap / unknown fails
assert_symbol_partition(live_engine_symbols(["EURUSD"]), ["EURUSD"])   # ok
for bad in (["NOPE"], ):
    try:
        assert_symbol_partition(ALL_SYMBOLS, bad); raise AssertionError("should reject unknown")
    except ValueError:
        pass
try:
    assert_symbol_partition(["EURUSD", "GBPUSD"], ["EURUSD"]); raise AssertionError("should reject overlap")
except ValueError:
    pass

print("OK")
