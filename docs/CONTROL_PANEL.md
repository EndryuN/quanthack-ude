# SIGNAL//LAYER — the operator control panel

A web dashboard for monitoring and (optionally) steering the live trading engine. It is a **separate process** that reads the engine's decision logs, equity samples, and live MetaTrader 5 state. By default it is strictly **read-only** and the engine is unaware it exists — the engine behaves byte-for-byte as it would without the panel.

> **Safety in one line:** every engine behaviour the panel can trigger is gated behind `EngineConfig.use_control_panel` (default `False`). With that flag off, the panel cannot affect trading at all. The kill-switch and risk floor always run first and can never be overridden by an operator control.

![SIGNAL//LAYER dashboard — left column (equity, positions/history, controls), main column (instrument & projection, three-model consensus dumbbell)](panel-dashboard.jpeg)

*The Dashboard view. Header bar (Market Read, refresh icon, AI-refresh countdown) and the pinned status/KPI strip across the top; a currency-strength ribbon on its own row; operator column on the left; signal column (instrument projection + Claude/Nemotron/DeepSeek consensus dumbbell) on the right. The risk gauges live in a popover off the KPI strip. Numbers shown are dry-run/sample data.*

---

## Running it

The normal way is the **interactive launcher** at the repo root — double-click `run_live.bat` (or run it from a terminal). It asks two questions, in order:

1. **Mode** — `[1] Live trading` or `[2] Dry-run` (dry-run sends no orders).
2. **Panel** — `[1] With panel` or `[2] No panel`.

So, for example, **live trading with panel = `1 1`**.

Choosing **With panel** does two things: it launches this dashboard in its own window (uvicorn on port 8800) **and** sets `QUANTHACK_PANEL=1` for the engine, which flips `use_control_panel` on so the operator controls actually steer the bot. Choosing **No panel** runs the engine exactly as it would with no panel code at all. The engine itself runs in the launcher window; press Ctrl+C there to stop it.

```text
===============================================
           QUANTHACK  LAUNCHER
===============================================

Mode  -  [1] Live trading   [2] Dry-run : 2
Panel -  [1] With panel   [2] No panel : 1

  Mode : DRY-RUN (no orders sent)
  Panel: ON  -  dashboard at http://127.0.0.1:8800
```

**Watch-only (no engine):** to bring up the dashboard against existing logs without touching the engine, run it standalone — it stays strictly read-only because `QUANTHACK_PANEL` is never set:

```powershell
# from the repo root (the launcher cd's there itself)
.\panel\run_panel.ps1
```

Then open **http://127.0.0.1:8800**. First load: do a hard refresh (Ctrl+Shift+R) — browsers cache the JS aggressively.

- It reads `logs/ai_decisions_*.jsonl`, `state/equity_samples.json`, and live MT5. Charts that need MT5 (live positions, the projection price line) degrade gracefully to an "unavailable" note when no terminal is connected; everything else renders from the logs alone.
- New deps: `fastapi`, `uvicorn` (already in `requirements.txt`). Charts use Plotly.js from a CDN.

---

## The layout

The dashboard (**SIGNAL//LAYER**) is a single-page app with two views, switched from the **hamburger menu** (top-left): **Dashboard** (the default) and **History**. A persistent **header bar** sits above both, with a **currency-strength ribbon** on its own row below it:

- **Brand + nav** — `SIGNAL//LAYER` and the hamburger menu (Dashboard / History). Clicking the brand returns to the Dashboard.
- **Market Read** button — opens a modal with all three models' full natural-language reasoning from the latest read, in stacked **Claude / Nemotron / DeepSeek** sections.
- **Refresh icon** (↻) — a spinning re-query button: it POSTs `/api/refresh`, which tells the engine to re-query the AI consensus **now**, skipping the 15-min cadence. The icon spins until a fresher decision lands.
- **AI-refresh countdown** — a `refreshing AI views in M:SS` clock counting down to the next scheduled 15-min refresh (it reads `refreshing AI views…` while a refresh is in flight, and goes amber/stale if the read ages well past its window).
- **Status / KPI strip** — pinned to the **right** of the header: live `EQUITY`, `RET` (return %), `LEV` (gross leverage), `ML` (margin level), `DD` (drawdown), `SHARPE`. **Hover or click it to reveal the Risk popover** (below).
- **Currency-strength ribbon** — its own row beneath the header, with a **Claude / Nemotron / DeepSeek / Combined** model selector on the left. Each currency/asset (USD, GBP, XAU, JPY, XAG, SOL…) is aggregated **net up/down across all its pairs** using the selected model's per-symbol views, shown with a direction arrow.

### Dashboard view

**Left column (operator):**
- **Equity Trend** — the equity curve with the **kill line drawn on it** (the −20% drawdown floor), framed tightly so real movement is visible.
- **Positions / History split** — open positions (refreshing every **1 s**, signed lots + live P&L, scalped symbols tagged) beside a rolling order/decision feed (`more →` jumps to the full History view).
- **Controls** — the steering knobs (Phase 2/3 below): a preset selector + Apply/Save; the `aggression` slider with **Flatten now** on the same row; the two AI **influence toggles** (`Nemotron input`, `DeepSeek input`); and the **Scalper** section.

The **Risk** panel is *not* a left-column card — it's a **popover** anchored off the status/KPI strip: **hover** the KPI strip to reveal it, **click** to pin it open, **✕** (or a click outside) to close. Its content is horizontal gauges (margin level, gross leverage, margin usage, drawdown) with **threshold ticks** (0 / competition tripwire / max) drawn on each bar, the bars re-colouring live as values move, plus the tripwire clocks.

**Main column (signal):**
- **Instrument & projection** — a symbol list grouped by asset class (FX / Metals / Crypto), the selected symbol's recent price, and a forward **projection** drawn as **one line per model** (Claude, Nemotron, DeepSeek — slope = confidence) **plus a pink Consensus line** = the running average of Claude and whichever 2nd-opinion voters are toggled on. This is an honest rendering of conviction — **not** a price forecast (the models output direction + confidence, not target levels). The chart carries a full toolset (pinned-instrument chips, line/candle, 1m/15m/1h, zoom/pan, draggable SL/TP, …) detailed in **[The instrument panel](#the-instrument-panel)** below.
- **Consensus** — tabbed **Latest** / **Over time**, with a per-model **hit-rate legend** in the card head (Claude / Nemotron / DeepSeek directional accuracy once scored, dimmed when a model didn't vote that read):
  - **Latest** is the **dumbbell** — per instrument, up to **three** markers: Claude (orange `#FF8A1E`), Nemotron (green `#76B900`), DeepSeek (blue `#4D6BFE`), each at `sign(direction) × confidence` on a short↔long axis. Markers that land on the same point **split into colored halves** (three coinciding fall back to concentric rings). A dotted connector spans each row: **green = the voting models agree**, **red = they disagree**, **muted when fewer than two voted**. It is **not** zoomable/pannable. Sub-tabs split it by **FX / Crypto / Metals**.
  - **Over time** is the **heatmap** — instruments × 15-min timesteps, coloured red→green by signed confidence (long = green, short = red), with **Claude / Nemotron / DeepSeek / Combined** layer buttons and a long/short legend.

    ![Consensus Over-time heatmap — instruments down the side, time across the bottom, cells red (short) to green (long)](panel-consensus-overtime.jpeg)

### History view
Per-symbol stance-over-time chart plus that symbol's order history — the audit trail for "what did the AI think about EURUSD all day, and what did we trade."

### The Scalper (engine-hosted)
The scalper is **not** a separate process or a separate view — it's an engine-hosted slot driven from the **SCALPER** section of the Controls card. It's a crypto fast-momentum strategy that runs *alongside* the engine on **at most two** instruments ceded to it. The flow is:

1. **Assign a symbol** — pick it from the dropdown and `Assign`; the engine cedes that instrument to the scalper.
2. **Walk-forward backtest gate** — assigning a symbol kicks off a walk-forward backtest (`/api/scalper/gate`); the verdict (`clears ✓` / `no edge`, with locked params) is persisted into `controls.json` and shown on the symbol's chip.
3. **Per-symbol arm** — each assigned symbol has its own `Arm` button. A gate-cleared symbol arms to **live**; one that didn't clear can only be **force-armed** (unvalidated) after a confirm. Un-armed symbols stay dry-run. Arming one symbol never disarms another. Live per-symbol state (watching / in-position / cooldown) shows inline.

See [STRATEGY.md](STRATEGY.md).

## The instrument panel

The **Instrument & projection** card is the panel's workbench. Its toolset:

- **Pinned-instrument chip bar** — pinned symbols show as chips across the top; click a chip to select it, **✕** to unpin.
- **Chart-type toggle** — **line** or **candle**.
- **Timeframe toggle** — **1m / 15m / 1h**.
- **Zoom / pan toggle** — left-drag either **pans** (the default) or box-**zooms**; scroll always zooms.
- **Reset-view** button — snaps the chart back to its default frame.
- **Current-price line** — a live dotted price line with a **price tag on the right axis**.
- **Open position** — the entry is drawn as a fixed **entry line** (signed lots + live P&L label).
- **Draggable Stop-Loss and Take-Profit lines** — drag either line to stage a new level; a **✓ / ✕ confirm bar tracks the line** (✓ saves it as an override, ✕ discards). A far stop/target never squashes the price view.
- **Stop Loss / Take Profit buttons** — create the line (at a sensible generated level) if none exists, otherwise locate the existing one and bring it into view to drag.
- **AI-view override row** — a direction (`AI view` / Force LONG / SHORT / FLAT) + confidence that **auto-applies on change** (no Apply button), plus a **Pin** button.

## The three control phases

The panel is built in three layers. Phase 1 is read-only; Phases 2–3 add the (default-off) control channel.

### Phase 1 — Watch (read-only, zero engine risk)
Everything described under **Dashboard / History** and the **Scalper** above reads only `logs/`, `state/`, and live MT5 (account / positions / bars). Charts that need MT5 degrade to an "unavailable" note when no terminal is connected; the rest renders from the logs alone.

### Phase 2 — Steer (global knobs + presets)
- **Knobs:** an `aggression` slider, **Flatten now** (panic close-all), and the two AI **influence toggles** (below). The refresh icon (re-query AI now) and the Scalper section also live in this layer.
- **Influence toggles** — *influence-only*; both models are always queried, recorded, and scored regardless:
  - **`Nemotron input`** (default **ON**) — gates whether the **algorithm** acts on Nemotron in sizing. Off = Claude-only sizing; Nemotron is still shown/recorded.
  - **`DeepSeek input`** (default **OFF**) — gates DeepSeek / ensemble influence in sizing. Off = DeepSeek is shown/recorded only.
- **Presets:** named bundles of the knobs — ships with `Default` (the tuned 0.30 aggression), `Survival` (0.0), `Finals` (1.0); create / save / switch / reset.

### Phase 3 — Weigh in (per-position)
- **Pin/lock** — the engine won't rebalance, close, or trail that position until you unpin.
- **Override as your view** — set a direction + confidence for a symbol; it becomes the deciding view alongside the AI models and *can flip the AI's direction*. Applies **immediately** (the POST bumps `apply_seq`), not at the next 15-min AI refresh.
- **Take-profit** — an optional per-position broker-side TP (the default strategy has no TP and trails the winner instead; this is a manual override on chosen positions), set via the draggable TP line.
- **Stop-loss** — an optional manual per-position SL, set via the draggable SL line. The engine honours it but **clamps it so it is never looser than the failsafe** — it can only tighten risk, never widen it.

---

## Enabling the control channel

The launcher does this for you: choosing **With panel** in `run_live.bat` sets `QUANTHACK_PANEL=1`, and `EngineConfig.use_control_panel` defaults to `os.environ.get("QUANTHACK_PANEL") == "1"` (see [`bot/config.py`](../bot/config.py)). So:

- **`run_live.bat` → With panel** → controls live (engine reads knobs/presets/overrides).
- **`run_live.bat` → No panel**, or the standalone `panel\run_panel.ps1` (which never sets the env var) → the engine ignores the control files; the UI is a pure monitor. Your bot is byte-for-byte unchanged.

To pin it on without the launcher, either export `QUANTHACK_PANEL=1` before starting the engine, or hard-set `use_control_panel = True` in `bot/config.py`. Either way, restart the engine for the change to take effect.

---

## How control flows to the engine (the contract)

The panel and engine communicate through small JSON files in `state/`, with a strict single-writer protocol:

| File | Written by | Read by | Contents |
|---|---|---|---|
| `state/controls.json` | panel (sole writer) | engine | `aggression`, `active_preset`, `second_opinion`, `ensemble_input`, the scalper fields (`scalper_symbols`, `scalper_armed`, `scalper_armed_force`, `scalper_gate`), and one-shot `flatten_seq` / `apply_seq` / `refresh_seq` |
| `state/presets.json` | panel (sole writer) | panel | named knob bundles |
| `state/overrides.json` | panel (sole writer) | engine | per-symbol `{pinned, view, take_profit, stop_loss, note}` |
| `state/control_ack.json` | **engine** (sole writer) | panel | processed one-shot sequence numbers (`flatten_seq_done`, `apply_seq_done`, `refresh_seq_done`) |

(`controls.json` still carries `block` / `allow` de-risk fields for backward-compatible plumbing, but the panel no longer exposes any per-symbol block/allow control.)

Cadence:
- **`flatten_seq`** (panic flatten) is honoured **intra-tick**, right after the kill-switch.
- **aggression / influence toggles / human view / TP / SL** apply on the *apply-forced reconcile*: the panel bumps `apply_seq` on every change, the engine forces a reconcile on the next tick, so a change takes effect within ~2 ticks (~10s). (Forcing it literally every tick would defeat the anti-churn gate and bleed spread.) Per-symbol overrides also bump `apply_seq`, so they apply immediately.
- **`refresh_seq`** (re-query AI now) makes the engine re-query the AI consensus on its next tick, **bypassing the 15-min cadence**. Triggered by the header refresh icon (`POST /api/refresh`); the engine acks via `refresh_seq_done`.

---

## Safety model

The control layer was designed and reviewed so it can never compromise the engine:

- **Default-off.** All engine integration is behind `use_control_panel` (default `False`) → flag-off behaviour is byte-for-byte identical to a build without the panel (verified branch-by-branch + by regression tests).
- **The risk floor is never gated.** `risk.assess` / the kill-switch / `flatten_all` / the soft de-risk path run **before** any control or override is even loaded. A pinned or human-overridden position **is still flattened by the kill-switch**.
- **Read-only Phase 1.** The dashboard only reads logs/state/MT5; it has no order/modify path. Live MT5 calls are read-only (account / positions / bars) and degrade cleanly when absent.
- **Dry-run sends nothing.** Operator flatten, take-profit, and stop-loss print intended actions in dry-run and never hit the broker.
- **A manual stop-loss can only tighten.** The engine clamps an operator SL so it is **never looser than the failsafe** — it can reduce risk, never widen it.
- **Single-writer + one-shot ack.** The engine never writes the control files; one-shot commands (`flatten_seq` / `apply_seq` / `refresh_seq`) use monotonic sequence numbers acknowledged in an engine-owned file, so there are no two-writer races.
- **A malformed control/override file can never crash the trading loop** — the accessors skip bad entries defensively.

---

## Architecture

```
 engine ──writes──▶ logs/ai_decisions_*.jsonl, state/equity_samples.json ──read──▶ panel backend (FastAPI)
   ▲                                                                                  │  reads logs + live MT5
   └── reads ── state/controls.json + state/overrides.json ◀── writes ── panel backend │  serves the SPA
                                  (engine writes only state/control_ack.json)          ▼
                                                                              panel frontend (Plotly SPA)
```

- **Backend:** `panel/backend/` — `data.py` (pure log/equity transforms), `projection.py` (projection geometry for the per-model + consensus lines), `app.py` (FastAPI routes + the control/override read-write API).
- **Frontend:** `panel/frontend/` — `index.html` / `styles.css` / `app.js`, the SIGNAL//LAYER theme and Plotly charts.
