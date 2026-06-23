"""Central configuration. Edit values here; everything reads from this."""
from dataclasses import dataclass, field

# The 15 competition instruments, by category. Names confirmed against the
# FTWorldwide broker via probe.py (exact matches, no suffixes).
FOREX  = ["AUDUSD", "EURCHF", "EURGBP", "EURUSD", "GBPUSD", "USDCAD", "USDCHF", "USDJPY"]
METALS = ["XAGUSD", "XAUUSD"]
CRYPTO = ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD", "BARUSD"]
ALL_SYMBOLS = FOREX + METALS + CRYPTO

INITIAL_EQUITY = 1_000_000.0   # competition starting capital (fixed)
ACCOUNT_LEVERAGE = 30          # broker max leverage (1:30)

# Broker-side stop-loss failsafe (per-position adverse % move). This is a
# CATASTROPHE backstop for when the bot is offline/crashed and the live
# kill-switch isn't running — set loose enough not to trip on normal vol.
# The live kill-switch + conservative leverage remain the primary protection
# against liquidation (which = auto-DQ).
SL_PCT = {"FX": 0.05, "METAL": 0.08, "CRYPTO": 0.15}

# Trailing-stop activation distance per class. Only once a position is up by
# this much does the trail ratchet the broker SL into profit (tracking `pct`
# behind price). Deliberately set to MEANINGFUL moves, not noise — trailing
# tighter than this whipsaws and bleeds spread (proven by backtest).
TRAIL_PCT = {"FX": 0.010, "METAL": 0.020, "CRYPTO": 0.040}


def asset_class(symbol: str) -> str:
    if symbol in CRYPTO:
        return "CRYPTO"
    if symbol in METALS:
        return "METAL"
    return "FX"


def sl_pct_for(symbol: str) -> float:
    return SL_PCT[asset_class(symbol)]


def trail_pct_for(symbol: str) -> float:
    return TRAIL_PCT[asset_class(symbol)]


@dataclass
class RiskLimits:
    """Hard guardrails. These are deliberately well inside the competition
    red-lines so brief spikes never trip a penalty, and a blow-up is
    structurally impossible before our own kill-switch fires."""

    # --- Anti-liquidation (the one that matters most) ---
    # Competition stop-out is margin_level=30%. We flatten FAR above that.
    kill_switch_margin_level: float = 150.0   # % ; flatten everything below this
    warn_margin_level: float = 250.0          # % ; block new risk below this

    # --- Effective leverage (rule tripwire starts >28x for >=30min) ---
    max_gross_leverage: float = 12.0          # gross notional / equity, hard cap
    target_gross_leverage: float = 6.0        # what sizing aims for when fully deployed

    # --- Margin usage (rule tripwire >90% for >=30min) ---
    max_margin_usage: float = 0.60            # used_margin / equity, hard cap

    # --- Concentration (rule tripwire >90% single instrument for >=30min) ---
    max_single_instrument_frac: float = 0.50  # fraction of gross notional in one symbol
    max_net_directional_frac: float = 0.85    # net directional / gross

    # --- Drawdown circuit breaker (our own, protects return + DD rank) ---
    daily_max_drawdown: float = 0.20          # de-risk hard if round DD exceeds this


@dataclass
class EngineConfig:
    dry_run: bool = True                      # True = compute & log, send NO orders
    loop_seconds: float = 5.0                 # main loop cadence
    order_deviation_points: int = 50          # max slippage tolerance on market orders
    magic: int = 990623                       # our order tag
    # BARUSD excluded: max notional ~$786 (tiny contract + 100-lot cap) makes it
    # untradeable in size — pure spread cost. Still in ALL_SYMBOLS for data.
    symbols: list = field(default_factory=lambda: [s for s in ALL_SYMBOLS if s != "BARUSD"])
    risk: RiskLimits = field(default_factory=RiskLimits)
    state_dir: str = "state"

    # --- signal source ---
    signal_source: str = "ai"                 # "ai" (Claude+sizer) or "hybrid"
    aggression: float = 0.30                  # 0=survival .. 1=finals (sizer dial)
    ai_refresh_seconds: float = 900.0         # re-query Claude every 15 min
    ai_effort: str = "high"
    use_broker_sl: bool = True                # attach broker-side SL failsafe
    use_trailing_stop: bool = True            # ratchet SL into profit on winners


CONFIG = EngineConfig()
