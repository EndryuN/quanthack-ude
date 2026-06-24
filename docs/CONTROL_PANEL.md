# SIGNAL//LAYER — the operator control panel

A web dashboard for monitoring and (optionally) steering the live trading engine. It is a **separate process** that reads the engine's decision logs, equity samples, and live MetaTrader 5 state. By default it is strictly **read-only** and the engine is unaware it exists — the engine behaves byte-for-byte as it would without the panel.

> **Safety in one line:** every engine behaviour the panel can trigger is gated behind `EngineConfig.use_control_panel` (default `False`). With that flag off, the panel cannot affect trading at all. The kill-switch and risk floor always run first and can never be overridden by an operator control.

---

## Running it

```powershell
# from the repo root (the launcher cd's there itself)
.\panel\run_panel.ps1
```

Then open **http://127.0.0.1:8800**. First load: do a hard refresh (Ctrl+Shift+R) — browsers cache the JS aggressively.

- It reads `logs/ai_decisions_*.jsonl`, `state/equity_samples.json`, and live MT5. Charts that need MT5 (live positions, the projection price line) degrade gracefully to an "unavailable" note when no terminal is connected; everything else renders from the logs alone.
- New deps: `fastapi`, `uvicorn` (already in `requirements.txt`). Charts use Plotly.js from a CDN.

---

## The three phases

The panel is built in three layers. Phase 1 is read-only; Phases 2–3 add the (default-off) control channel.

### Phase 1 — Watch (read-only, zero engine risk)
- **Consensus dumbbell** — per instrument, Claude (violet) and Nemotron (teal) confidence + direction, with a connecting line that encodes agreement (chartreuse = agree/boosted, red = disagree).
- **Consensus heatmap** — instruments × 15-min timesteps, coloured red→green by signed confidence.
- **Equity Trend** — the equity curve with the **risk-floor lines drawn on it** (the −20% drawdown kill level), framed tightly so real movement is visible.
- **Risk gauges + tripwire clocks** — margin level, gross leverage, margin usage, drawdown against the real `RiskLimits`, plus how long each 30-minute competition tripwire has been live.
- **Per-symbol price + projection cones** — recent price (15m/1h, scroll/zoom) with a forward directional cone per model (slope = confidence, width = volatility). This is an honest rendering of conviction + uncertainty — **not** a price forecast (the models output direction + confidence, not target levels).
- **Rolling orders feed** and the symbol list grouped by asset class (FX / Metals / Crypto).

### Phase 2 — Steer (global knobs + presets)
- **Knobs:** an `aggression` slider, global **Pause** (no new risk), **Flatten now** (panic close-all), and per-symbol **allow/block** (a de-risk clamp — it never force-closes).
- **Presets:** named bundles of the knobs — ships with `Default` (the tuned 0.30 aggression), `Survival` (0.0), `Finals` (1.0); create / save / switch / reset.

### Phase 3 — Weigh in (per-position)
- **Pin/lock** — the engine won't rebalance, close, or trail that position until you unpin.
- **Override as your view** — set a direction + confidence for a symbol; it becomes the deciding "third opinion" alongside Claude and Nemotron and *can flip the AI's direction*.
- **Take-profit** — an optional per-position broker-side TP (the default strategy has no TP and trails the winner instead; this is a manual override on chosen positions).
- **Per-ticker note** — a persistent objective you write per instrument, shown alongside the AI's live rationale.

---

## Enabling the control channel

1. Set `use_control_panel = True` in [`bot/config.py`](../bot/config.py).
2. Restart the engine.
3. Run the panel. Knobs/presets and per-position controls now take effect.

Until you do all three, the controls in the UI write files that the engine simply ignores — your bot is unchanged.

---

## How control flows to the engine (the contract)

The panel and engine communicate through small JSON files in `state/`, with a strict single-writer protocol:

| File | Written by | Read by | Contents |
|---|---|---|---|
| `state/controls.json` | panel (sole writer) | engine | paused, aggression, block/allow, active preset, one-shot `flatten_seq`/`apply_seq` |
| `state/presets.json` | panel (sole writer) | panel | named knob bundles |
| `state/overrides.json` | panel (sole writer) | engine | per-symbol `{pinned, view, take_profit, note}` |
| `state/control_ack.json` | **engine** (sole writer) | panel | processed one-shot sequence numbers |

Cadence:
- **`flatten_seq`** (panic flatten) is honoured **intra-tick**, right after the kill-switch.
- **Pause / block / aggression / human view** apply on the *apply-forced reconcile*: the panel bumps `apply_seq` on every change, the engine forces a reconcile on the next tick, so a change takes effect within ~2 ticks (~10s). (Forcing it literally every tick would defeat the anti-churn gate and bleed spread.)

---

## Safety model

The control layer was designed and reviewed so it can never compromise the engine:

- **Default-off.** All engine integration is behind `use_control_panel` (default `False`) → flag-off behaviour is byte-for-byte identical to a build without the panel (verified branch-by-branch + by regression tests).
- **The risk floor is never gated.** `risk.assess` / the kill-switch / `flatten_all` / the soft de-risk path run **before** any control or override is even loaded. A pinned or human-overridden position **is still flattened by the kill-switch**.
- **Read-only Phase 1.** The dashboard only reads logs/state/MT5; it has no order/modify path. Live MT5 calls are read-only (account / positions / bars) and degrade cleanly when absent.
- **Dry-run sends nothing.** Operator flatten and take-profit print intended actions in dry-run and never hit the broker.
- **Single-writer + one-shot ack.** The engine never writes the control files; one-shot commands use monotonic sequence numbers acknowledged in an engine-owned file, so there are no two-writer races.
- **Block = de-risk clamp, not close.** Blocking a symbol forbids new/increased risk; it never actively closes a position.
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

- **Backend:** `panel/backend/` — `data.py` (pure log/equity transforms), `projection.py` (cone geometry), `app.py` (FastAPI routes + the control/override read-write API).
- **Frontend:** `panel/frontend/` — `index.html` / `styles.css` / `app.js`, the SIGNAL//LAYER theme and Plotly charts.
