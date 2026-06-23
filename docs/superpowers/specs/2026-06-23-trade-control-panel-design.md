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

## 10. Open items / future
- Replay/scrubber mode for demos (seed dramatic moments).
- Realized per-trade P&L attribution (depends on data-gap fix #3).
- NVIDIA-green theming intensity for prize emphasis (a toggle).
- Audible alerts on tripwire/kill events (operational use).
