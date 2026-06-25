"""The runtime Nemotron toggle: control override beats config, but never on
without an API key. Pure logic, no MT5/AI."""
from bot.controls import second_opinion_on, DEFAULT_CONTROLS

# default control value is None -> defer to config
assert DEFAULT_CONTROLS["second_opinion"] is None

# controls=None (panel gated off) -> use the config default, gated by the key
assert second_opinion_on(None, config_default=True, key_present=True) is True
assert second_opinion_on(None, config_default=True, key_present=False) is False
assert second_opinion_on(None, config_default=False, key_present=True) is False

# control override None -> still the config default
assert second_opinion_on({"second_opinion": None}, True, True) is True

# control override False -> OFF even when config + key say on (the user's kill)
assert second_opinion_on({"second_opinion": False}, True, True) is False

# control override True -> ON even when config default is off...
assert second_opinion_on({"second_opinion": True}, False, True) is True
# ...but never on without a key
assert second_opinion_on({"second_opinion": True}, False, False) is False

print("OK")
