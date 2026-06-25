"""Central configuration. Edit values here; everything reads from this."""
import os
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

# Trailing-stop ARM distance per class: the trail only activates once a position
# is up by this much in its favor (a MEANINGFUL move, not noise).
TRAIL_ACTIVATE_PCT = {"FX": 0.010, "METAL": 0.020, "CRYPTO": 0.040}
# Once armed, the stop trails this far BEHIND price. Tighter than the arm distance
# so arming immediately locks real profit (arm 1% / trail 0.5% FX -> ~+0.5% locked)
# instead of only break-even (the old behavior used one value for both).
TRAIL_PCT = {"FX": 0.005, "METAL": 0.010, "CRYPTO": 0.020}


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


def trail_activate_pct_for(symbol: str) -> float:
    return TRAIL_ACTIVATE_PCT[asset_class(symbol)]


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
class ScalperConfig:
    """Separate mean-reversion scalper (own process, own magic). OFF by default.
    Its `symbols` are EXCLUSIVE: when enabled they are removed from the AI
    engine's universe so the two never net into each other on this netting
    account. Signal params are starting values, refined by the backtest gate."""
    enabled: bool = False
    symbols: list = field(default_factory=lambda: ["EURUSD"])
    lots: dict = field(default_factory=lambda: {"EURUSD": 0.5})
    ema_period: int = 20
    entry_bps: float = 5.0
    exit_bps: float = 1.0
    tp_bps: float = 4.0
    stop_bps: float = 8.0
    time_stop_bars: int = 10
    poll_secs: float = 2.0
    max_concurrent: int = 2
    daily_loss_limit: float = 2000.0     # account currency
    magic: int = 990624


SCALPER = ScalperConfig()


def engine_symbols(scalper_enabled: bool, scalper_symbols) -> list:
    """The AI engine's tradable universe: all symbols minus BARUSD, and minus
    the scalper's symbols ONLY when the scalper is enabled (so flag-off behavior
    is identical to before the scalper existed)."""
    ceded = set(scalper_symbols) if scalper_enabled else set()
    return [s for s in ALL_SYMBOLS if s != "BARUSD" and s not in ceded]


@dataclass
class EngineConfig:
    dry_run: bool = True                      # True = compute & log, send NO orders
    loop_seconds: float = 5.0                 # main loop cadence
    order_deviation_points: int = 50          # max slippage tolerance on market orders
    magic: int = 990623                       # our order tag
    # BARUSD excluded: max notional ~$786 (tiny contract + 100-lot cap) makes it
    # untradeable in size — pure spread cost. Still in ALL_SYMBOLS for data.
    symbols: list = field(default_factory=lambda: engine_symbols(SCALPER.enabled, SCALPER.symbols))
    risk: RiskLimits = field(default_factory=RiskLimits)
    state_dir: str = "state"

    # --- signal source ---
    signal_source: str = "ai"                 # "ai" (Claude+sizer) or "hybrid"
    aggression: float = 0.30                  # 0=survival .. 1=finals (sizer dial)
    ai_refresh_seconds: float = 900.0         # re-query Claude every 15 min
    ai_effort: str = "high"
    use_broker_sl: bool = True                # attach broker-side SL failsafe
    use_trailing_stop: bool = True            # ratchet SL into profit on winners

    # --- currency-factor exposure cap (block-only; OFF by default, runtime toggle) ---
    # Caps NET per-currency exposure (a long USDCHF + long USDCAD both stack USD).
    # Block-only: holds an exposure-WORSENING add at current size when consensus is
    # weak; never reduces a position; strong consensus always passes. See exposure.py.
    use_exposure_cap: bool = False            # master default; toggle live via the panel
    exposure_cap_frac: float = 1.0            # max |net currency notional| / equity
    exposure_strong_conf: float = 0.70        # consensus >= this is always allowed through

    # --- operator control panel (Phase 2; OFF by default = identical to today) ---
    # Default reads the QUANTHACK_PANEL env var (set by the launcher's "with panel"
    # choice); unset/"0" -> False -> byte-for-byte identical to no panel.
    use_control_panel: bool = field(
        default_factory=lambda: os.environ.get("QUANTHACK_PANEL") == "1")

    # --- AI decision logging ---
    log_decisions: bool = True                # write per-day decision logs
    log_dir: str = "logs"                     # directory for ai_decisions_*.{jsonl,md}

    # --- Nemotron second opinion (NVIDIA hosted, OpenAI-compatible API) ---
    use_second_opinion: bool = True           # master switch (auto-off if no key)
    nemotron_model: str = "nvidia/nemotron-3-super-120b-a12b"
    nemotron_consensus_gain: float = 0.5      # agreement boost strength, [0,1]
    second_opinion_timeout: float = 30.0      # seconds; failure -> Claude-only

    # --- multi-model ensemble via the Pydantic AI Gateway (Logfire/Doubleword) ---
    # OFF by default. When on, each gateway model votes additively on Claude's
    # direction (bot/ensemble.py); needs GATEWAY_BASE_URL + GATEWAY_API_KEY env.
    use_ensemble: bool = False
    ensemble_models: list = field(default_factory=list)   # gateway model ids to poll
    ensemble_gain: float = 0.5                # voter agree/disagree strength, [0,1]
    ensemble_timeout: float = 30.0


CONFIG = EngineConfig()
