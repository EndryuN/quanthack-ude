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


def is_crypto_alt(symbol: str) -> bool:
    """Crypto alts (ETH/SOL/XRP/...) use the BTC lead-lag confirmation. BTC
    itself leads (no self lead-lag) and FX/metals are uncorrelated to BTC, so
    they scalp on pure momentum — applying the lead-lag to them just adds noise."""
    return symbol in CRYPTO and symbol != "BTCUSD"


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
    """The integrated scalper SLOT (engine-hosted, own magic for attribution).
    OFF by default. Symbols are assigned at runtime via the panel and ceded from
    the engine's universe. Phase 2 fills the slot with a crypto fast-momentum
    strategy (per-tick, BTC->alt lead-lag, broker TP/SL); params are starting
    values refined by the walk-forward arming gate (bot/crypto_backtest.py)."""
    enabled: bool = False
    symbols: list = field(default_factory=lambda: ["BTCUSD"])
    lots: dict = field(default_factory=lambda: {"BTCUSD": 1.0})
    # crypto momentum params (the live strategy)
    lookback: int = 5             # momentum/breakout window (bars)
    entry_bps: float = 20.0       # breakout threshold (must clear crypto cost)
    lead_lookback: int = 3        # BTC lookback for the alt lead-lag confirmation
    lead_bps: float = 10.0        # min BTC move to count as a lead
    tp_bps: float = 20.0          # broker take-profit (fast exit)
    stop_bps: float = 20.0        # broker stop-loss
    cooldown_secs: float = 30.0   # min seconds between opens/flips per symbol (anti-churn)
    max_trades_per_hour: int = 12 # hard turnover cap per symbol (anti-runaway)
    min_hold_secs: float = 60.0   # no scalper-initiated close/flip before this (broker TP/SL still exit)
    notional_per_symbol: float = 50_000.0   # USD risk budget per scalped symbol -> lots
    # legacy fade params (kept for the dormant scalp_backtest reference only)
    ema_period: int = 20
    exit_bps: float = 1.0
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


def live_engine_symbols(assigned) -> list:
    """The engine's tradable universe at RUNTIME: all symbols minus BARUSD and
    minus the dynamically-assigned scalper symbols (computed each tick so a panel
    assignment cedes the symbol live, spec §9.4)."""
    ceded = set(assigned or [])
    return [s for s in ALL_SYMBOLS if s != "BARUSD" and s not in ceded]


def assert_symbol_partition(engine_syms, scalper_syms) -> None:
    """Fail-fast invariant (spec §3.2): scalper symbols must be real instruments
    and disjoint from the engine's universe."""
    bad = [s for s in scalper_syms if s not in ALL_SYMBOLS]
    if bad:
        raise ValueError(f"scalper symbols not in ALL_SYMBOLS: {bad}")
    overlap = set(engine_syms) & set(scalper_syms)
    if overlap:
        raise ValueError(f"engine/scalper symbol overlap (must be disjoint): {sorted(overlap)}")


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
