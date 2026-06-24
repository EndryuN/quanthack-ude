# Trade Control Panel — Design Spec

**Date:** 2026-06-23
**Status:** Draft for review
**Author:** Andrew Nguyen (with Claude + a 4-agent design panel)

## 1. Goal

A live, visually distinctive **control panel** for the AI trading bot that does three
things, built in phases:

1. **See** what the bot is doing — the Claude × Nemotron consensus, the equity
   curve against the risk floor, live risk gauges, and per-instrument intent.
2. **Steer** the bot live — global risk-posture knobs saved as presets.
3. **Weigh in** on individual positions — pin/lock, override the AI's view as a
   third opinion, and set a take-profit — plus a per-ticker objective note.

The panel is also the demo artifact for the NVIDIA "Best Technology Project"
prize: it makes the dual-AI (Claude + Nemotron) consensus and the survival-first
risk discipline visible and interactive.

## 1a. Hard constraints — DO NOT DISRUPT THE LIVE COMPETITION BOT (binding)

The engine is running live in the competition *while this panel is being built.*
These bind every task and override anything else in this spec on conflict:

- **Default-off.** A new `EngineConfig.use_control_panel: bool = False` gates ALL
  engine integration. With it False (the competing default), the engine reads no
  control files and behaves **byte-for-byte as today**. The panel is invisible to
  the running bot until the operator opts in.
- **Read-only Phase 1.** Phase 1 (the dashboard) makes **zero** engine changes — it
  only reads `logs/`, `state/`, and live-MT5 read-only methods. Safe to build and
  run against the live bot immediately.
- **Isolated development.** All code lands on a feature branch (`feat/control-panel`),
  never on the branch the operator runs. Engine changes take effect only when the
  operator pulls AND restarts AND enables the flag.
- **Additive & backward-compatible.** Missing/partial control files → current
  behavior. No control input may weaken the risk floor (Resolution C2).
- **The risk floor is never gated.** The kill-switch, `flatten_all`, and the soft
  de-risk path run BEFORE and INDEPENDENT of every control/override (Resolution C2).

## 2. Scope

Built in three phases; **each phase is usable on its own**.

- **Phase 1 — Read-only dashboard.** The SIGNAL//LAYER UI over existing data. No
  engine changes (beyond optional data-gap logging). Ships a demoable artifact.
- **Phase 2 — Global knobs + presets.** Live control of risk posture
  (aggression, allow/block, pause, flatten) with named presets; `Default` = our
  tuned config. First two-way channel to the engine.
- **Phase 3 — Per-position control + per-ticker notes.** Pin/lock, human view
  override (third opinion), user-set take-profit, and the AI-reason + user-note
  per ticker. The heaviest engine changes.

**Out of scope:** changing the core strategy/sizer/risk math; backtesting UI;
mobile; multi-user/auth; cloud hosting. The panel runs locally beside the engine.

## 3. Architecture

The engine and the panel are **separate local processes** that communicate
through small JSON state files (the engine already owns `state/` and writes
`logs/`). No change to the engine's authority model — the kill-switch and risk
floor remain absolute.

```
 ┌────────────┐   writes   logs/ai_decisions_*.jsonl
 │   ENGINE   │ ─────────▶ state/equity_samples.json ─────────┐
 │ (bot loop) │                                               │ read
 │            │   reads    state/controls.json   ◀──────────┐ │
 │            │ ◀───────── state/overrides.json  ◀────────┐ │ │
 └────────────┘                                           │ │ ▼
       ▲                                          writes  │ │ ┌──────────────┐
       │ live MT5 (positions/account)                     │ └─│   BACKEND    │
       └──────────────────────────────────────────────────  │ │  (FastAPI)   │
                                                             └─│  reads logs  │
                                                               │  + live MT5  │
                                                               └──────┬───────┘
                                                          HTTP / SSE  │
                                                               ┌──────▼───────┐
                                                               │  FRONTEND    │
                                                               │ custom SPA   │
                                                               │ (SIGNAL//    │
                                                               │  LAYER UI)   │
                                                               └──────────────┘
```

- **Backend (FastAPI + uvicorn):** reads `logs/ai_decisions_*.jsonl`,
  `state/equity_samples.json`, and (optionally) live MT5 via the existing
  `MT5Client`; serves the frontend; exposes REST endpoints that read/write the
  control files. Streams updates to the frontend via Server-Sent Events (SSE)
  or short polling.
- **Frontend:** a custom single-page app (HTML/CSS/vanilla-JS or a light
  framework) implementing the SIGNAL//LAYER visual system, with a charting
  library (Plotly.js or ECharts via local bundle).
- **Engine integration:** the engine reads `controls.json` / `overrides.json`
  at its decide→execute seam each 5s tick (see §7). The control files are the
  contract; the backend never calls the engine directly.

### Why this stack
The "cool, custom UI" requirement rules out Streamlit (hard to theme). The
two-way control channel needs a backend. A FastAPI backend + custom frontend
gives full visual control *and* the live control loop. New deps: `fastapi`,
`uvicorn` (both small, pure-Python-friendly).

## 4. Data sources & required data-gap fixes

### Sources (existing)
- **`logs/ai_decisions_*.jsonl`** — per 15-min refresh: account snapshot,
  Claude/Nemotron regimes, per-symbol views (direction, confidence, both models'
  rationale, agreement, boosted), intended orders, cost. Glob + concat across
  days. ~96 records/day (<1 MB/week).
- **`state/equity_samples.json`** — equity curve at ~15-min buckets (`peak`,
  `max_dd`). The clean time series for the equity chart.
- **Live MT5 (`MT5Client`)** — `account()`, `positions()` (with live `profit`),
  `tick()`, `bars()`. Windows + terminal required.

### Data-gap fixes (the logged data is otherwise misleading)
Surfaced by the feasibility review. These are engine-side and should land so the
panel is truthful:

1. **Fill confirmation (Phase 1/2).** `orders[]` currently logs *intended*
   orders, not confirmed fills. Capture `ok`/`retcode` from
   `client.market_order`/`close_symbol` in `_do_order`/`_do_close` and add
   `"filled": bool, "retcode": int` to each order record. (~10 lines.)
2. **Per-instrument unrealized P&L (Phase 1).** Add `"unrealized_pnl": float`
   to each view record from `client.position_for(sym).profit`, so the panel can
   show per-ticker P&L. (~5 lines.)
3. **SL-hit / closed-trade capture (Phase 3, optional).** Broker SL/TP closes
   are invisible to the log → unexplained equity drops. Add a
   `history_deals_get` wrapper to `MT5Client`, poll recent deals each refresh,
   and log `{"event":"trade_closed","symbol","price","profit"}`. Enables
   accurate realized P&L attribution. (~30 lines; defer if time-constrained.)
4. **Logs are local-only.** `logs/` is gitignored — fine for local operation
   (the panel reads the local dir). Only relevant if you want sample logs in the
   repo for handoff; if so, commit a small redacted sample, not live data.

## 5. Visual system — "SIGNAL//LAYER"

Adopted from the design panel. A research-terminal aesthetic: dense data,
deliberate whitespace, one expressive accent.

**Palette**

| Name | Hex | Use |
|---|---|---|
| Canvas | `#0D0E12` | background |
| Surface | `#14161D` | panels/cards |
| Lift | `#1E2330` | hover, flat cells |
| Chartreuse | `#C8FF00` | primary accent — equity line, confirmed consensus |
| Violet | `#7B61FF` | **Claude** identity |
| Teal | `#00C8A0` | **Nemotron** identity |
| Amber | `#F5A623` | **Human** override identity |
| Long | `#2ECC71` / Short `#FF4D4D` / Flat `#3A3F50` | direction |
| Ink `#E8EAF0` / Muted `#6B7080` | text |

(Chartreuse appears only on genuine agreement, so it stays meaningful. NVIDIA
green leanings optional for prize emphasis.)

**Typography:** Space Grotesk (display/regime/KPIs), IBM Plex Sans (body/
rationale), JetBrains Mono (all numeric data, orders).

**Signature charts**
- **Consensus dumbbell (snapshot):** one row per instrument; Claude confidence
  on the left, Nemotron on the right, a connecting line whose style encodes
  agreement (solid chartreuse = agree+boosted, dashed = agree, red = disagree,
  none = unavailable). When a human override exists, an amber marker overrides
  the row.
- **Consensus heatmap (run-over-time):** instruments × 15-min timesteps; cell
  hue = direction, luminosity (3 bands) = confidence, corner badge = consensus
  state. Horizontal scroll + minimap.
- **Equity curve:** step-line in chartreuse with the **risk-floor lines drawn
  on it** — the 20% drawdown kill level and the 12x leverage cap — as
  ever-present references you never cross. Drawdown as a shaded band.
- **Per-symbol price + model projection cones:** recent price (line/candles)
  with a **forward directional projection per model** — Claude (violet),
  Nemotron (teal), and the consensus/final (chartreuse). Each is a forward
  vector from the current price whose **slope is scaled by that model's
  confidence** and whose **cone width is scaled by the instrument's volatility**
  (already computed by the sizer via `ret_vol`). This honestly renders
  conviction + uncertainty — NOT a price forecast (the models output
  direction+confidence, not target levels). A human override (amber) draws its
  own cone. Entry/exit/SL markers overlaid from the order history.
- **Risk gauges:** arc dials for Margin Level, Gross Leverage, Margin Usage,
  Drawdown, with threshold bands at the real limits (150/250% ML, 12x lev, 60%
  margin, 20% DD) and **30-min tripwire countdown clocks** (lev>25x, margin>85%,
  concentration>85%).

## 6. Features by phase

### Phase 1 — Read-only dashboard
- Top strip: timestamp, mode (DRY/LIVE), Claude regime (violet) + Nemotron
  regime (teal), account KPIs.
- Equity curve with risk-floor lines; risk gauges + tripwire clocks.
- Consensus dumbbell (latest decision) + consensus heatmap (the run).
- Per-symbol price chart with **forward projection cones per model** (slope =
  confidence, width = volatility); pulls recent bars from live MT5
  (`client.bars`), degrading to the historical cache when MT5 is absent.
- Current positions table (symbol, side, lots, live P&L, SL, % of gross).
- Order activity feed; AI cost meter (Claude + Nemotron).
- "Next refresh in m:ss" countdown.
- Auto-updates via SSE/polling; degrades to JSONL-only if MT5 is absent.

### Phase 2 — Global knobs + presets
- **Knobs:** `aggression` slider (0–1), global **Pause** (engine computes but
  sends no new orders), **Flatten now** (one-shot), per-symbol **allow/block**.
- **Presets:** named bundles of the knobs. Ships with `Default` (our tuned
  config: aggression 0.30 + current allow-all), `Survival` (0.0), `Finals`
  (1.0) along the existing sizer aggression axis. Create / rename / switch /
  reset-to-Default. Persisted in `state/presets.json`.
- Writing a knob updates `state/controls.json`; the engine reads it next tick.

### Phase 3 — Per-position control + per-ticker notes
- **Select a position →** drill-in panel with:
  - **Pin/lock:** engine will not rebalance or close it until unpinned.
  - **Override as your view:** set direction + confidence; becomes the deciding
    third opinion for that symbol (can boost, cut, or **flip** the AI). Applies
    at the next 15-min decision. Amber identity throughout.
  - **Stop-loss:** shown read-only (engine-managed: catastrophe failsafe +
    trailing).
  - **Take-profit:** user-set price (optional, per-position). The engine sets a
    broker-side TP. Default remains no-TP / trail-the-winner; this is a manual
    override on chosen positions only.
- **Per-ticker note:** the AI's live rationale (Claude + Nemotron, from the log)
  shown alongside a **persistent user-authored objective** ("USDJPY = USD/rates
  proxy…"), editable, stored in `state/overrides.json`.

## 7. Engine integration contract

> **Refined by §9a Review Resolutions — those govern on any conflict** (control-read
> cadence, single-writer/ack protocol, pin-freezes-trailing, block=de-risk-clamp,
> `set_sltp`, human-override layer). All of this is gated by `use_control_panel`
> (default off); with it off the engine reads nothing here.

The engine reads two files at the **decide→execute seam** (between producing
views and reconciling). The risk floor / kill-switch run **before** any of this
and are never affected.

### `state/controls.json` (app writes, engine reads each tick)
```jsonc
{
  "paused": false,            // true → reconcile sends no NEW orders (de-risk still allowed)
  "flatten_now": false,       // one-shot → flatten all, engine resets to false
  "aggression": 0.30,         // overrides CONFIG.aggression in the sizer
  "active_preset": "Default",
  "block": ["XAGUSD"],        // symbols forced to flat target (no new risk)
  "allow": null               // if a list, ONLY these may take new risk
}
```

### `state/overrides.json` (per-symbol; app writes, engine reads)
```jsonc
{
  "EURUSD": {
    "pinned": true,                                   // skip reconcile for this symbol
    "view": { "direction": "long", "confidence": 0.70 } | null,  // human third opinion
    "take_profit": 1.0850 | null,                     // broker-side TP price
    "note": "USD/rates proxy; trade yield divergence" // persistent objective (display only)
  }
}
```

### Engine behavior changes
1. **Load controls/overrides** at the top of the reconcile block (cheap file
   reads; tolerate missing/partial files → defaults).
2. **Aggression:** pass `controls.aggression` to `build_targets` instead of
   `cfg.aggression`.
3. **Human override:** apply a post-consensus layer — for any symbol with
   `overrides[sym].view`, replace that symbol's final `(direction, confidence)`
   with the human's (strongest voice; can flip direction). Record it in the
   decision log as a `"human"` field on the view and `agreement:"human_override"`.
4. **Block/allow:** blocked symbols get target 0 (de-risk only); if `allow` is
   set, all symbols not in it get target 0.
5. **Pin:** `_reconcile` skips pinned symbols entirely (no rebalance, no close).
6. **Pause:** `_reconcile` performs no order that increases/opens exposure
   (de-risking and trailing stops still run).
7. **Flatten_now:** flatten all, then the engine rewrites `controls.json` with
   the flag cleared.
8. **Take-profit:** for each symbol with `overrides[sym].take_profit`, set the
   broker-side TP via a new `client.set_tp(symbol, price)` (mirror of the
   existing `set_sl`), applied like trailing stops each loop.

All control inputs are **advisory to the strategy layer only** — the risk
engine's kill-switch, leverage/margin caps, and de-risk-only mode still sit
below and cannot be overridden by the panel.

## 8. Tech stack

- **Backend:** FastAPI + uvicorn. Reuses `bot/` modules (`MT5Client`, config).
  New deps: `fastapi`, `uvicorn`.
- **Frontend:** custom SPA (HTML/CSS/JS), charting via Plotly.js or ECharts
  (vendored locally so the demo works offline). No build step required if using
  vanilla JS + a CDN-or-vendored chart lib.
- **Data:** `pandas.read_json(lines=True)` over globbed daily JSONL.
- **Live updates:** SSE from the backend (file-watch / 5s poll → push).

## 9. Risks

- **MT5 coupling:** live positions/P&L need the Windows terminal. Mitigation:
  every panel degrades to JSONL-only; MT5 data is additive.
- **Control-file race:** engine and app both touch `state/`. Mitigation: atomic
  writes (`.tmp` + rename, the pattern `state.py` already uses); engine reads are
  best-effort with defaults.
- **Human override vs. safety:** an override must never defeat the risk floor.
  Mitigation: overrides apply only at the strategy layer; risk assessment and
  kill-switch run first and unconditionally.
- **Take-profit departs from strategy:** a fixed TP caps trend upside.
  Mitigation: opt-in per position; trailing-stop default unchanged.
- **Scope:** this is a multi-part build. Mitigation: the phasing — Phase 1 ships
  value with zero engine risk.
- **Demo without live data:** Mitigation: a small replay mode that walks a
  day's JSONL (Phase 1 nice-to-have).

## 9a. Review resolutions (2026-06-24) — these GOVERN §7 on conflict

A code-grounded review found seams that would otherwise force guesswork or risk
the live bot. Resolutions (binding):

**Control-read cadence (was C3/I3; REFINED 2026-06-24 after implementation).**
- **Intra-tick, right after the kill-switch, independent of `_signal_changed()`:**
  `flatten_seq` (one-shot panic flatten) acts within one 5s loop.
- **On the apply-forced reconcile, NOT literally every tick:** `pause`, `block`/
  `allow` de-risk, and `aggression`. The panel bumps `apply_seq` on every control
  change; the engine sees the new `apply_seq`, forces a reconcile on the next tick
  (`_last_acted_ts = -1`), and the reconcile re-reads the current controls and
  applies the pause/block clamp + aggression. So a control change takes effect
  within ~2 ticks (~10s). **It must NOT be applied literally every tick:** forcing
  a reconcile every 5s while a symbol is merely *blocked* (not paused) would
  re-evaluate all non-blocked targets every tick and defeat the anti-churn gate
  (spread bleed). `pause` is also re-read on every subsequent AI-refresh reconcile,
  so the engine never opens new risk while paused regardless of cadence.

**Single-writer + one-shot commands (was C4).** The app is the **sole writer** of
`controls.json` and `overrides.json`; the engine **never writes them**. One-shot
commands (`flatten_now`) carry a monotonic `seq`; the engine acts and records the
processed `seq` in an engine-owned `state/control_ack.json`. The app clears the
command after seeing the ack. All writes use the atomic `.tmp`+`os.replace`
pattern from `state.py`. This removes the two-writer lost-update hazard.

**Pin = full freeze (was C1).** A pinned symbol is skipped in `_reconcile` **and**
in `_update_trailing_stops` (no rebalance, no close, no SL ratchet). Pin is read
only in those strategy-layer paths.

**Risk floor ignores all overrides — tested invariant (was C2).** `risk.assess`/
`enforce`/`flatten_all` and the soft `_derisk_only` path run before and independent
of every control/override. A pinned or human-overridden position **is still
flattened by the kill-switch**. Required test: with a position pinned, drive margin
level below the kill threshold and assert `flatten_all` closes it anyway.

**Human override layer (was I2).** Add `apply_human_override(sizer_views, records,
overrides)` (in `bot/second_opinion.py`), called **after** `apply_consensus` in
`_refresh_ai_views`. It maps `"long"/"short"/"flat" → 1/-1/0`, overwrites
`sizer_views[sym]`, and rewrites that record entry: adds a `"human": {direction,
confidence}` field, sets `agreement:"human_override"`, `boosted:false`. The
decision-log MD renderer already falls through to the raw `agreement` string, so
the new value renders without change.

**Block/allow = de-risk clamp, NOT close (was I4).** Block does **not** set target
0 (that would actively close the position). It clamps the target toward flat with
the same semantics as `_derisk_only`: forbid increasing/opening, allow reduction.
`allow` (if set) clamps every symbol not listed the same way. The `controls.json`
comment is corrected accordingly. A separate explicit "close X now" is out of
scope (use Flatten or set a human flat view).

**`set_sltp` preserves both legs (was I1).** Replace the proposed `set_tp` with
`client.set_sltp(symbol, sl, tp)` that reads the position's current SL/TP and
preserves the leg not being changed (the existing `set_sl` zeroes TP — a naive
`set_tp` and the trailing stop would clobber each other every loop). Add `sl`/`tp`
to the `Position` dataclass. The backend validates a user TP is on the profitable
side of current price before writing it.

**Live per-ticker P&L from MT5, not the log (was I5).** Phase-1 live per-ticker
P&L comes from the backend reading live MT5 `positions()[].profit` directly. Drop
the proposed `unrealized_pnl` log field (it would be 15-min stale and misleading).

**Phasing (data-gap fixes are an optional sub-track).** Phase 1's value (charts,
gauges, consensus viz, projection cones) does **not** depend on any engine change.
The data-gap fixes (fill confirmation, SL-hit capture) are a clearly-separate,
optional low-risk engine sub-track, each behind the default-off posture, never a
prerequisite for the read-only dashboard.

## 10. Open items / future
- Replay/scrubber mode for demos (seed dramatic moments).
- Realized per-trade P&L attribution (depends on data-gap fix #3).
- NVIDIA-green theming intensity for prize emphasis (a toggle).
- Audible alerts on tripwire/kill events (operational use).
