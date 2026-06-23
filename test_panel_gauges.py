"""Risk gauges + tripwire flags derived from an account snapshot."""
from panel.backend.data import risk_gauges, tripwire_status

acct = {"equity": 1019620, "gross_leverage": 3.1, "margin_usage": 0.11,
        "margin_level": 937, "return_pct": 0.0196, "drawdown": 0.001, "sharpe": -0.19}

g = {x["key"]: x for x in risk_gauges(acct)}
assert set(g) == {"margin_level", "gross_leverage", "margin_usage", "drawdown"}
assert g["gross_leverage"]["value"] == 3.1
assert any(h["at"] == 12 for h in g["gross_leverage"]["hard"]), "12x cap marked"
assert any(h["at"] == 150 for h in g["margin_level"]["hard"]), "150% kill marked"
assert g["margin_usage"]["unit"] == "%" and abs(g["margin_usage"]["value"] - 11.0) < 1e-6

# safe state -> no tripwire active
tw = {x["key"]: x for x in tripwire_status(acct)}
assert tw["leverage"]["active"] is False and tw["margin"]["active"] is False

# breach state
hot = dict(acct, gross_leverage=26.0, margin_usage=0.88)
tw2 = {x["key"]: x for x in tripwire_status(hot)}
assert tw2["leverage"]["active"] is True and tw2["leverage"]["penalty"] == 28
assert tw2["margin"]["active"] is True
print("OK")
