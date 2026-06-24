# Scalper System — Design Spec

*Date: 2026-06-24*

## Motivation

The competition's **Final Score is dominated by Sharpe/consistency, not raw return** — the leaderboard #1 (Sharpe 0.85, +$67k) outscores players who made 8× the P&L (Sharpe ~0.11). Our engine sits at rank 11 with Sharpe ~0.09 and a 33% win rate: returns are fine, *consistency is the bottleneck*. The leader climbs by running a high-frequency, high-win-rate, low-variance BTC scalp — a Sharpe machine. This project adds a **separate, isolated scalping system** that trades a configurable set of symbols on a mean-reversion micro-edge, to lift Sharpe **without disturbing the existing AI engine**.

This is **evidence-first**: the scalper ships disabled and is only armed live after a backtest shows the edge clears costs out-of-sample.

## Hard constraints

- **The live AI engine must not be disrupted.** No change to its signal, sizing, risk, or trailing logic. The only shared-code change is a *no-op-today* magic filter on `client.positions()` and the removal of the scalper's symbols from the AI engine's universe.
- **Separate process, separate magic.** The scalper runs as its own process (`python -m bot.scalper`), tags orders `magic=990624` (the engine uses `990623`).
- **Dry-run by default.** Sends no orders unless `--live`. `ScalperConfig.enabled=False` by default.
- **Account is `RETAIL_NETTING`** (verified: login 10363, FTWorldwide-MainTrade). One net position per symbol broker-wide → the scalper and the AI engine must **never trade the same symbol**.
- **Never crash / never threaten the account.** The scalper has its own daily-loss kill-switch; the account-wide kill-switch (margin level) still governs everything. ASCII-only console output (the engine pipes stdout → cp1252).
- **No new third-party dependencies.**

## Key findings that shaped the design

1. **Netting account** → isolation by **symbol-exclusivity**, not magic alone. Magic-filtering two systems on one symbol does not work when the broker holds a single net position per symbol.
2. **Spreads (live):** BTCUSD ≈ **$5.03 per 1-lot round-trip** (5029 pts × 0.001, contract 1.0); **EURUSD spread = 0**; XAUUSD ≈ $26/lot round-trip. A mean-reversion scalp on a **zero-spread FX pair has no cost to overcome** and is far more likely to be net-positive than BTC.
3. **Backtest data:** local **tick-level** parquet (`pricer-output-*/`) exists for FX + metals + oil (~1 month, May 11–Jun 10), **no crypto**. The M15 cache is FX+metals only. → backtest the edge on zero-spread FX (rich data, zero cost); BTC, if configured, is validated on shallower **MT5-pulled M1** history.

## Architecture

Three new units + two minimal touch-points in shared code.

### `bot/scalp_signal.py` (pure, no MT5 — fully unit-testable)
The mean-reversion fade decision, as a pure function of a recent price series.
- Input: a sequence of recent closes (M1) for one symbol + the `ScalperConfig` params for that symbol + the current open position state (none / long / short, entry price).
- Compute: short EMA over `ema_period`; `deviation_bps = (price − ema)/ema × 1e4`.
- **Entry (when flat):** short if `deviation_bps > +entry_bps`; long if `deviation_bps < −entry_bps`; else flat.
- **Exit (when in a position):** flat (close) if any of —
  - revert-to-mean: `deviation_bps` crosses back through ~0 (sign flip vs entry side), or within `exit_bps` of the mean;
  - take-profit: favorable move ≥ `tp_bps`;
  - max-adverse stop: adverse move ≥ `stop_bps`;
  - time stop: held ≥ `time_stop_bars`.
- Output: a target signal `{-1, 0, +1}` (the desired net position direction) + a reason string. Pure; no side effects.

### `bot/scalper.py` (the loop + execution + scalper risk)
- A standalone `Scalper` class + `python -m bot.scalper [--live]` entrypoint. Dry-run unless `--live`.
- Its own `MT5Client(magic=990624)`.
- Loop (every `poll_secs`, default ~2s, or on M1 close): for each configured symbol, pull recent M1 closes (`client.bars(symbol, M1, n)`), read the scalper's own position for that symbol, call `scalp_signal`, and reconcile toward the target via the client (open/close fixed `lots`). One position per symbol at a time (netting).
- **Scalper risk gate (runs first, like the engine's pattern):**
  - `max_concurrent`: cap on simultaneously open scalper positions.
  - **daily-loss kill-switch:** track realized P&L for the trading day; if ≤ `−daily_loss_limit`, flatten all scalper positions and halt for the rest of the day.
- ASCII-only console output; a per-day log (mirroring `decision_log`'s file approach) of scalp entries/exits with realized P&L is **out of scope for v1** (stdout only) — may be added later.
- Dry-run prints intended actions and sends nothing.

### `ScalperConfig` (in `bot/config.py`)
```
enabled: bool = False
symbols: list[str] = ["EURUSD"]        # exclusive to the scalper; default zero-spread FX
lots: dict[str, float] = {"EURUSD": 0.5}  # fixed size per symbol
ema_period: int = 20                   # M1 bars
entry_bps: float = 5.0                  # deviation to enter
exit_bps: float = 1.0                   # near-mean exit band
tp_bps: float = 4.0                     # take-profit
stop_bps: float = 8.0                   # max adverse
time_stop_bars: int = 10                # max hold (M1 bars)
poll_secs: float = 2.0
max_concurrent: int = 2
daily_loss_limit: float = 2000.0        # account currency
magic: int = 990624
```
The signal params (`entry_bps`/`exit_bps`/`tp_bps`/`stop_bps`/`time_stop_bars`)
are **starting values, refined by the backtest gate** before the system is armed
live — the backtest's parameter sweep produces the values that ship enabled.

### Shared-code touch-points (minimal, reviewed)
1. **`bot/client.py` `positions()` — add an optional magic filter.** A new optional parameter (default = filter to `self.magic`) so each `MT5Client` reads only its own positions. **No-op today** (account holds only `magic=990623` positions). This gives clean per-system attribution and means neither system's reconcile/risk/trailing sees the other's positions. *Reviewed against the live engine: with only engine positions on the account, filtered == unfiltered, so engine behavior is unchanged.*
2. **AI engine universe** — the scalper's configured symbols are **removed from `cfg.symbols`** so the AI engine never trades them. (Default: EURUSD leaves the AI universe and becomes the scalper's. The user chooses the trade-off when configuring scalped symbols.)

## Backtest — the evidence gate

`bot/scalp_backtest.py` + `run_scalp_backtest.py`:
- Load the local tick parquet for a symbol, aggregate to **M1** (reuse the dataloader's aggregation approach; strict no-lookahead).
- Replay `scalp_signal` bar-by-bar, simulating fills at the **realistic per-symbol spread/cost** (EURUSD ≈ 0; configurable).
- Report: **trade count, win rate, net P&L (after costs), Sharpe (competition's 15-min definition), max drawdown**, and a small parameter sweep over `entry_bps`/`tp_bps`/`stop_bps`.
- **Gate to go live:** out-of-sample net-positive after costs **and** Sharpe meaningfully above the AI engine's, with a sane trade count. Only then is `ScalperConfig.enabled` flipped and the system armed `--live`.
- BTC (no local data): validated on MT5-pulled M1 history (`copy_rates`), flagged as a thinner sample.

## Testing

- **`scalp_signal`** — unit tests for each branch: entry thresholds (long/short/flat), each exit condition (mean, TP, stop, time), no-lookahead. Pure, no MT5.
- **Scalper risk** — `max_concurrent` cap and daily-loss kill-switch (flatten + halt) via a FakeClient; dry-run sends nothing.
- **Isolation** — a test asserting the scalper's configured symbols are NOT in the AI engine's `cfg.symbols`, and that `client.positions(magic=...)` filters correctly.
- **Backtest** — determinism on a fixed slice; cost accounting sanity (zero-spread → no cost drag).

## Out of scope (v1)

- Panel integration for the scalper (a later add).
- A per-day scalp decision log file (stdout only for v1).
- Multiple concurrent positions per symbol (netting → one per symbol).
- Order-flow / passive market-making (not viable on market-order retail feed).

## Success criteria

A separate scalper process that (a) leaves the AI engine byte-for-byte unchanged with the flag off, (b) trades only its own exclusive symbols with fixed small size + a daily-loss kill-switch, (c) has a backtested mean-reversion edge that clears costs out-of-sample before it's armed, and (d) is dry-run by default. The intent is a low-footprint Sharpe booster.
