"""Read-only dashboard backend. Reads the engine's logs/state files and (when
available) live MT5 via the existing read-only MT5Client. NEVER writes engine
state and NEVER imports anything that mutates trading. The trading engine is
untouched by this process."""
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import data, projection
from bot import controls as ctrl
from bot import overrides as ov_mod

_HERE = os.path.dirname(os.path.abspath(__file__))
FRONTEND = os.path.join(_HERE, "..", "frontend")
# Resolve the engine dirs relative to the REPO ROOT (parent of panel/) so the
# dashboard works no matter which directory uvicorn was launched from.
# Overridable in tests / via env.
_ROOT = os.path.dirname(os.path.dirname(_HERE))
LOG_DIR = os.environ.get("PANEL_LOG_DIR", os.path.join(_ROOT, "logs"))
STATE_DIR = os.environ.get("PANEL_STATE_DIR", os.path.join(_ROOT, "state"))

app = FastAPI(title="SIGNAL//LAYER")


def _mt5():
    """Return a connected read-only MT5Client, or None if unavailable.
    Read-only: we only call account()/positions()/bars(). PANEL_NO_LIVE=1 forces
    offline mode (no MT5 at all) so the panel renders purely from logs/state —
    used for the demo/replay dashboard and to avoid spawning the MT5 terminal."""
    if os.environ.get("PANEL_NO_LIVE") == "1":
        return None
    try:
        from bot.client import MT5Client
        c = MT5Client()
        c.connect()
        return c
    except Exception:
        return None


@app.get("/api/state")
def state():
    recs = data.load_decisions(LOG_DIR)
    last = data.latest_decision(recs)
    if last is None:
        return {"account": None, "gauges": [], "tripwires": [], "consensus": [],
                "regime": {}, "models": {}, "cost": {}, "ts": None}
    acct = dict(last["account"])
    # Overlay the LIVE account so the risk gauges react in real time — the logged
    # account is only as fresh as the last 15-min decision. Falls back to the
    # logged values if MT5 is unavailable. (margin_level / margin_usage / gross
    # leverage track the market each poll; drawdown stays the engine's round
    # figure from the log since it needs the equity-peak history.)
    realized = None
    c = None if os.environ.get("PANEL_NO_LIVE") == "1" else _mt5()
    if c is not None:
        try:
            a = c.account()
            eq = a.equity or acct.get("equity") or 1.0
            acct["equity"] = a.equity
            try:
                realized = c.realized_profit()
            except Exception:
                realized = None
            gross = sum(abs(c.notional(p.symbol, p.volume)) for p in c.positions())
            acct["gross_leverage"] = gross / eq if eq else acct.get("gross_leverage", 0.0)
            if a.margin and a.margin > 0:        # real numbers only when positions are open
                acct["margin_level"] = a.margin_level
                acct["margin_usage"] = a.margin / eq if eq else acct.get("margin_usage", 0.0)
        except Exception:
            pass
        finally:
            try:
                c.shutdown()
            except Exception:
                pass
    import datetime as _dt
    try:   # age of the AI views (local naive clock, same as the engine's _iso)
        _age = (_dt.datetime.now() - _dt.datetime.strptime(last["ts"], "%Y-%m-%dT%H:%M:%S")).total_seconds()
    except Exception:
        _age = None
    return {
        "ts": last["ts"], "mode": last.get("mode"),
        "views_age_secs": _age,
        "account": acct,
        "gauges": data.risk_gauges(acct),
        "tripwires": data.tripwire_status(acct),
        "consensus": data.consensus_rows(last),
        "regime": data.normalize_regime(last.get("regime", {})),
        "models": last.get("models", {}),
        "cost": last.get("cost", {}),
        "orders": last.get("orders", []),
        "recent_orders": data.recent_orders(recs),
        "realized": realized,
    }


@app.get("/api/decisions")
def decisions():
    return {"records": data.load_decisions(LOG_DIR)}


@app.get("/api/heatmap")
def heatmap():
    return data.heatmap_matrix(data.load_decisions(LOG_DIR))


@app.get("/api/equity")
def equity():
    return {"series": data.equity_series(STATE_DIR)}


@app.get("/api/symbols")
def symbols():
    """Tradable instruments (for the scalper assignment dropdown)."""
    from bot.config import ALL_SYMBOLS
    return {"symbols": [s for s in ALL_SYMBOLS if s != "BARUSD"]}


# static field reference + example — always available (incl. offline). Mirrors
# bot/ai_signal.build_context and docs/DECISION_FLOW.md (appendix) / STRATEGY.md §2.
_CTX_FIELDS = [
    {"field": "px", "desc": "last close of the most recent M15 bar"},
    {"field": "ret 1h / 4h / 1d", "desc": "% return over 4 / 16 / 96 M15 bars"},
    {"field": "ret 3d", "desc": "% return over 72 H1 bars"},
    {"field": "trend M15 / H1 / H4", "desc": "up/dn from an EMA cross (20/80, 10/40, 6/24)"},
    {"field": "mom_z", "desc": "32-bar momentum z-score (return / per-bar σ·√n)"},
    {"field": "vol/bar", "desc": "per-bar return volatility over 48 M15 bars (%)"},
    {"field": "5d-range", "desc": "price position in the last 120 H1 bars' high-low band (0=low, 100=high)"},
]
_CTX_EXAMPLE = ("EURUSD: px=1.08423 | ret 1h=-0.04 4h=+0.11 1d=-0.23 3d=+0.87% | "
                "trend M15=up H1=up H4=dn | mom_z=+0.41 vol/bar=0.06% | 5d-range=61%")
_CTX_NOT_FED = ["raw ticks / candle OHLC / individual bars", "order book, bid/ask spread, volume, open interest",
                "news, fundamentals, any text/sentiment feed", "account / equity / margin / position state",
                "one model's answer to another (Nemotron & DeepSeek are blind peers)"]


def _parse_ctx_line(line: str) -> dict:
    """Parse one build_context line into fields (the raw line stays authoritative)."""
    row = {"symbol": (line.split(":", 1)[0].strip() if ":" in line else "?"), "line": line}
    try:
        for seg in (s.strip() for s in line.split(":", 1)[1].split("|")):
            if seg.startswith("px="):
                row["px"] = seg[3:]
            elif seg.startswith("ret "):
                row["ret"] = {k: v for k, v in (t.split("=", 1) for t in seg[4:].split() if "=" in t)}
            elif seg.startswith("trend "):
                row["trend"] = {k: v for k, v in (t.split("=", 1) for t in seg[6:].split() if "=" in t)}
            elif "mom_z=" in seg:
                for t in seg.split():
                    if t.startswith("mom_z="): row["mom_z"] = t[6:]
                    elif t.startswith("vol/bar="): row["vol_bar"] = t[8:]
            elif seg.startswith("5d-range="):
                row["range_5d"] = seg[9:]
    except Exception:
        pass
    return row


@app.get("/api/context")
def context():
    """Informational: the EXACT context fed to the LLMs each AI-views refresh — the
    SYSTEM prompt, the user-message template, and the per-instrument snapshot from
    bot.ai_signal.build_context. Needs MT5 for the live snapshot; degrades offline to
    the prompt + template + static field reference (so the demo page is never blank)."""
    import datetime as _dt
    from bot.ai_signal import SYSTEM, MODEL
    from bot.config import CONFIG, ALL_SYMBOLS, live_engine_symbols
    try:
        assigned = ctrl.scalper_assignments(ctrl.load_controls(STATE_DIR), ALL_SYMBOLS)
        syms = live_engine_symbols(assigned)
    except Exception:
        syms = [s for s in ALL_SYMBOLS if s != "BARUSD"]
    user_template = ("Instruments and recent action (competition universe = exactly these {N} symbols):"
                     "\n\n{CONTEXT}\n\nGive a directional view + calibrated confidence for each.")
    base = {"system": SYSTEM, "user_template": user_template, "model": MODEL,
            "cadence_secs": CONFIG.ai_refresh_seconds, "symbols": syms,
            "field_reference": _CTX_FIELDS, "example_line": _CTX_EXAMPLE, "not_fed": _CTX_NOT_FED}
    c = _mt5()
    if c is None:
        return {**base, "available": False, "raw": None, "rows": [],
                "note": "Live snapshot needs the running engine (MT5). Showing the prompt, template and field reference."}
    try:
        from bot.ai_signal import build_context
        raw = build_context(c, syms)
        rows = [_parse_ctx_line(l) for l in raw.split("\n") if l.strip()]
        return {**base, "available": True, "raw": raw, "rows": rows,
                "ts": _dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")}
    except Exception as e:
        return {**base, "available": False, "raw": None, "rows": [],
                "note": f"context build failed: {e}"}
    finally:
        try: c.shutdown()
        except Exception: pass


@app.get("/api/volatility")
def volatility(tf: str = "15m"):
    """INFORMATIONAL (panel-only, not wired into the engine): a live realized-vol
    index from current MT5 bars on the chosen timeframe (15m or 1h). Per symbol =
    std of returns over 48 bars; a robust z-score vs the symbol's own rolling
    baseline makes classes comparable (crypto's absolute vol dwarfs FX). Aggregated
    to Combined + FX/Metals/Crypto. Needs MT5; degrades to available:false offline."""
    import datetime as _dt
    c = _mt5()
    if c is None:
        return {"available": False, "tf": tf, "note": "Live volatility needs the running engine (MT5)."}
    try:
        import numpy as _np
        import MetaTrader5 as _mtlib
        from bot.config import ALL_SYMBOLS, asset_class
        timeframe = {"15m": _mtlib.TIMEFRAME_M15, "1h": _mtlib.TIMEFRAME_H1}.get(tf, _mtlib.TIMEFRAME_M15)
        per = {}
        for s in (x for x in ALL_SYMBOLS if x != "BARUSD"):
            rates = c.bars(s, timeframe, 160)
            if rates is None or len(rates) < 70:
                continue
            closes = _np.asarray(rates["close"], dtype=float)
            rets = _np.diff(closes) / closes[:-1]
            if len(rets) < 58:
                continue
            W = 48
            series = _np.array([rets[i - W:i].std() for i in range(W, len(rets) + 1)])  # rolling realized vol
            cur = float(series[-1])
            med = float(_np.median(series))
            mad = float(_np.median(_np.abs(series - med)))
            z = (cur - med) / (1.4826 * mad + 1e-12)
            per[s] = {"raw": round(cur * 100, 3), "z": round(max(-4.0, min(4.0, z)), 2),
                      "cls": asset_class(s)}
        def _agg(cls):
            zs = [v["z"] for v in per.values() if cls is None or v["cls"] == cls]
            return round(sum(zs) / len(zs), 2) if zs else None
        return {"available": True, "tf": tf, "ts": _dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                "combined": _agg(None), "fx": _agg("FX"), "metals": _agg("METAL"),
                "crypto": _agg("CRYPTO"), "per_symbol": per,
                "method": f"{tf} ret_vol(48), robust z vs own rolling baseline"}
    except Exception as e:
        return {"available": False, "tf": tf, "note": f"volatility build failed: {e}"}
    finally:
        try: c.shutdown()
        except Exception: pass


@app.get("/api/model_scores")
def model_scores():
    """Per-model directional hit-rates (Claude / Nemotron / DeepSeek) from
    state/model_scores.json. {} until the engine has scored two refreshes."""
    import json as _json
    try:
        with open(os.path.join(STATE_DIR, "model_scores.json"), encoding="utf-8") as f:
            return _json.load(f)
    except (OSError, ValueError):
        return {"rates": {}, "n": 0}


@app.get("/api/scalper")
def scalper_state():
    """Per-symbol scalper status (state/scalper.json) for the inline progress
    display under Positions. {} when the scalper hasn't emitted yet."""
    import json as _json
    path = os.path.join(STATE_DIR, "scalper.json")
    try:
        with open(path, encoding="utf-8") as f:
            return _json.load(f)
    except (OSError, ValueError):
        return {"symbols": []}


@app.get("/api/positions")
def positions():
    c = _mt5()
    if c is None:
        return {"available": False, "positions": []}
    try:
        from bot.config import SCALPER, ALL_SYMBOLS
        # a position is "scalped" if its symbol is ASSIGNED to the scalper (the
        # operator's intent) OR it's actually held under the scalper's magic.
        assigned = set(ctrl.scalper_assignments(ctrl.load_controls(STATE_DIR), ALL_SYMBOLS))
        out = [{"symbol": p.symbol, "lots": p.signed_lots,
                "profit": getattr(p, "profit", 0.0),
                "entry": getattr(p, "price_open", 0.0),
                "sl": getattr(p, "sl", 0.0), "tp": getattr(p, "tp", 0.0),
                "scalped": p.symbol in assigned or getattr(p, "magic", 0) == SCALPER.magic}
               for p in c.positions(magic=None)]
        return {"available": True, "positions": out}
    finally:
        try:
            c.shutdown()
        except Exception:
            pass


@app.get("/api/bars/{symbol}")
def bars(symbol: str, tf: str = "15m"):
    """Recent bars + each model's projection cone for one symbol, on the 15m or
    1h timeframe. Needs live MT5; degrades to {available:false} otherwise."""
    c = _mt5()
    if c is None:
        return {"available": False}
    try:
        import MetaTrader5 as mt5
        import datetime as _dt
        _TF = {"1m": mt5.TIMEFRAME_M1, "15m": mt5.TIMEFRAME_M15, "1h": mt5.TIMEFRAME_H1,
               "4h": mt5.TIMEFRAME_H4, "1d": mt5.TIMEFRAME_D1}
        timeframe = _TF.get(tf, mt5.TIMEFRAME_M15)
        _N = {"1m": 1500, "15m": 1500, "1h": 1500, "4h": 1500, "1d": 2000}   # max bars to return
        _LOOKBACK_DAYS = {"1m": 7, "15m": 45, "1h": 240, "4h": 540, "1d": 2200}  # how far back to ASK
        n_cap = _N.get(tf, 1000)
        # Pull a DATE RANGE first — this forces the terminal to download deeper history
        # from the broker than copy_rates_from_pos has cached. Fall back to from_pos if
        # the range path returns nothing (never regress below the old behavior).
        rates = None
        try:
            date_from = _dt.datetime.now() - _dt.timedelta(days=_LOOKBACK_DAYS.get(tf, 30))
            rr = c.bars_range(symbol, timeframe, date_from)
            if rr is not None and len(rr) > 0:
                rates = rr[-n_cap:]
        except Exception:
            rates = None
        if rates is None or len(rates) < 20:
            rates = c.bars(symbol, timeframe, n_cap)
        if rates is None or len(rates) < 20:
            return {"available": False}
        live = [(int(rates["time"][i]), float(rates["open"][i]), float(rates["high"][i]),
                 float(rates["low"][i]), float(rates["close"][i])) for i in range(len(rates))]
        # DEEPEN beyond the broker's shallow cache:
        #  - crypto -> Binance (years, free) for the whole series (no broker seam);
        #  - FX/metals -> prepend the backtest pricer cache (same prices -> seamless).
        # Best-effort; on any failure we keep the live MT5 series.
        from panel.backend import history as _hist
        from bot.config import asset_class
        cls = asset_class(symbol)
        series = None
        if cls == "CRYPTO":
            bn = _hist.binance_history(symbol, tf, n_cap)          # deep for all timeframes
            if len(bn) >= 20:
                series = bn
        elif tf in ("1h", "4h", "1d"):
            yh = _hist.yahoo_history(symbol, tf)                   # deep FX/metals history (1h+)
            if len(yh) >= 20:
                series = yh
        if series is not None and live:
            # anchor the external feed to the broker's latest price (multiplicative,
            # so % moves are preserved) — removes feed basis (esp. metals futures vs
            # spot) so the position / SL / TP overlays still line up.
            bl, el = live[-1][4], series[-1][4]
            if el:
                f = bl / el
                series = [(t, o * f, h * f, l * f, c * f) for (t, o, h, l, c) in series]
        if series is None:
            older = _hist.parquet_history(symbol, tf)             # [] for crypto / missing
            cutoff = live[0][0]
            series = [b for b in older if b[0] < cutoff] + live if older else live
        series = series[-n_cap:]
        times = [b[0] for b in series]
        opens = [b[1] for b in series]
        highs = [b[2] for b in series]
        lows = [b[3] for b in series]
        closes = [b[4] for b in series]
        price = closes[-1]
        rets = [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))]
        vol = (sum(r * r for r in rets[-48:]) / max(len(rets[-48:]), 1)) ** 0.5
        last = data.latest_decision(data.load_decisions(LOG_DIR))
        view = next((v for v in (last or {}).get("views", []) if v["symbol"] == symbol), None)
        cones = {}
        if view:
            cones["consensus"] = projection.projection_cone(price, view["direction"], view["confidence"], vol)
            if view.get("claude"):
                cones["claude"] = projection.projection_cone(price, view["claude"]["direction"], view["claude"]["confidence"], vol)
            if view.get("nemotron"):
                cones["nemotron"] = projection.projection_cone(price, view["nemotron"]["direction"], view["nemotron"]["confidence"], vol)
            ds = data._deepseek_of(view)   # DeepSeek gateway voter (from views[].voters)
            if ds:
                cones["deepseek"] = projection.projection_cone(price, ds["direction"], ds["confidence"], vol)
        return {"available": True, "symbol": symbol, "times": times,
                "opens": opens, "highs": highs, "lows": lows,
                "closes": closes, "vol": vol, "cones": cones}
    finally:
        try:
            c.shutdown()
        except Exception:
            pass


def _controls_path():
    return os.path.join(STATE_DIR, "controls.json")


@app.get("/api/controls")
def get_controls():
    return ctrl.load_controls(STATE_DIR)


@app.post("/api/controls")
def post_controls(body: dict):
    c = ctrl.load_controls(STATE_DIR)
    for k in ("paused", "aggression", "block", "allow", "active_preset",
              "second_opinion", "ensemble_input", "scalper_symbols", "scalper_armed",
              "scalper_armed_force"):
        if k in body:
            c[k] = body[k]
    # gated arming (spec §3.2), PER SYMBOL: a symbol may be armed only if it
    # cleared the gate or is in the force list. Drop any armed symbol that fails
    # this (so arming/gating one symbol never disarms another). Engine re-checks.
    arm_error = None
    gates = c.get("scalper_gate") or {}
    force = set(c.get("scalper_armed_force") or [])
    keep, rejected = [], []
    for s in (c.get("scalper_armed") or []):
        if (gates.get(s) or {}).get("cleared") or s in force:
            keep.append(s)
        else:
            rejected.append(s)
    c["scalper_armed"] = keep
    c["scalper_armed_force"] = [s for s in force if s in keep]   # prune stale force flags
    if rejected:
        arm_error = f"not gate-cleared: {', '.join(rejected)}"
    c["apply_seq"] = int(c.get("apply_seq", 0)) + 1   # apply knobs within one tick
    ctrl.atomic_write_json(_controls_path(), c)
    return {**c, "arm_error": arm_error} if arm_error else c


@app.get("/api/scalper/gate")
def scalper_gate(symbol: str):
    """Run the walk-forward backtest gate for `symbol` (the 'backtest as a step'
    in arming) and persist the verdict + locked params into controls for the
    engine's live-eligibility check (spec §3.1)."""
    from bot.crypto_backtest import gate_for_symbol
    eng_sharpe = 0.1
    try:
        last = data.latest_decision(data.load_decisions(LOG_DIR))
        if last:
            eng_sharpe = float(last["account"].get("sharpe", 0.1))
    except Exception:
        pass
    res = gate_for_symbol(symbol.upper(), engine_sharpe=eng_sharpe)
    c = ctrl.load_controls(STATE_DIR)
    gates = dict(c.get("scalper_gate") or {})
    gates[symbol.upper()] = {"cleared": bool(res.get("arm")), "reason": res.get("reason"),
                             "params": res.get("params"), "test": res.get("test"),
                             "cost_bps": res.get("cost_bps")}
    c["scalper_gate"] = gates
    ctrl.atomic_write_json(_controls_path(), c)
    return res


@app.post("/api/flatten")
def post_flatten():
    c = ctrl.load_controls(STATE_DIR)
    c["flatten_seq"] = int(c.get("flatten_seq", 0)) + 1
    ctrl.atomic_write_json(_controls_path(), c)
    return c


@app.post("/api/refresh")
def post_refresh():
    """Operator 'refresh consensus now': bump refresh_seq so the engine re-queries
    AI views on its next tick, bypassing the 15-min cadence."""
    c = ctrl.load_controls(STATE_DIR)
    c["refresh_seq"] = int(c.get("refresh_seq", 0)) + 1
    ctrl.atomic_write_json(_controls_path(), c)
    return c


def _presets_path():
    return os.path.join(STATE_DIR, "presets.json")


def _load_presets():
    import json as _json
    try:
        with open(_presets_path(), encoding="utf-8") as f:
            data = _json.load(f)
        if isinstance(data, dict) and data:
            return data
    except (OSError, ValueError):
        pass
    return {k: dict(v) for k, v in ctrl.DEFAULT_PRESETS.items()}


@app.get("/api/presets")
def get_presets():
    return {"presets": _load_presets(),
            "active": ctrl.load_controls(STATE_DIR).get("active_preset", "Default")}


@app.post("/api/presets/apply")
def apply_preset(body: dict):
    name = body.get("name")
    if not name:
        return {"error": "preset name required"}
    presets = _load_presets()
    if name not in presets:
        return {"error": f"unknown preset {name}"}
    p = presets[name]
    c = ctrl.load_controls(STATE_DIR)
    c["aggression"] = p.get("aggression")
    c["block"] = list(p.get("block") or [])
    c["allow"] = p.get("allow")
    c["active_preset"] = name
    c["apply_seq"] = int(c.get("apply_seq", 0)) + 1
    ctrl.atomic_write_json(_controls_path(), c)
    return c


@app.post("/api/presets/save")
def save_preset(body: dict):
    name = body.get("name")
    if not name:
        return {"error": "preset name required"}
    c = ctrl.load_controls(STATE_DIR)
    presets = _load_presets()
    presets[name] = {"aggression": c.get("aggression"),
                     "block": list(c.get("block") or []), "allow": c.get("allow")}
    ctrl.atomic_write_json(_presets_path(), presets)
    return {"presets": presets, "active": c.get("active_preset", "Default")}


def _overrides_path():
    return os.path.join(STATE_DIR, "overrides.json")


@app.get("/api/overrides")
def get_overrides():
    return ov_mod.load_overrides(STATE_DIR)


@app.post("/api/overrides/{symbol}")
def post_override(symbol: str, body: dict):
    o = ov_mod.load_overrides(STATE_DIR)
    entry = dict(o.get(symbol) or {"pinned": False, "view": None,
                                   "take_profit": None, "stop_loss": None, "note": ""})
    # sanitize a human view so a malformed body can never reach the engine loop
    if body.get("view") is not None:
        vw = body["view"] or {}
        d = str(vw.get("direction", "flat")).lower()
        if d not in ("long", "short", "flat"):
            d = "flat"
        try:
            c = float(vw.get("confidence", 0.5))
        except (TypeError, ValueError):
            c = 0.5
        if c != c or c in (float("inf"), float("-inf")):   # NaN / inf guard
            c = 0.5
        mode = str(vw.get("mode", "override")).lower()
        if mode not in ("override", "additive"):
            mode = "override"
        body["view"] = {"direction": d, "confidence": max(0.0, min(1.0, c)), "mode": mode}
    for k in ("pinned", "view", "take_profit", "stop_loss", "note"):
        if k in body:
            entry[k] = body[k]
    o[symbol] = entry
    ctrl.atomic_write_json(_overrides_path(), o)
    # apply immediately: nudge the engine to reconcile on its next ~5s tick (so a
    # Force LONG/SHORT/FLAT or take-profit doesn't wait for the 15-min AI refresh)
    c = ctrl.load_controls(STATE_DIR)
    c["apply_seq"] = int(c.get("apply_seq", 0)) + 1
    ctrl.atomic_write_json(_controls_path(), c)
    return o


@app.get("/api/scalp/symbols")
def scalp_symbols():
    """The instruments the Scalper page can backtest (BARUSD excluded — it's
    untradeable in size). Crypto needs a running MT5 terminal for M1 history."""
    from bot.config import ALL_SYMBOLS, CRYPTO
    return {"symbols": [s for s in ALL_SYMBOLS if s != "BARUSD"],
            "needs_mt5": [s for s in CRYPTO if s != "BARUSD"]}


@app.get("/api/scalp/backtest")
def scalp_backtest(symbol: str = "EURUSD"):
    """Run the entry/tp/stop sweep for one symbol and return the grid + best row.
    Crypto pulls M1 live from MT5; FX/metals use the local tick parquet. This is
    a read-only analysis — it never trades. Synchronous (blocks the worker for a
    few seconds while it loads bars); fine for a single-operator panel."""
    from bot.scalp_backtest import sweep_scalp
    return sweep_scalp(symbol.upper())


@app.get("/")
def index():
    return FileResponse(os.path.join(FRONTEND, "index.html"))


# static assets (styles.css, app.js) served from the frontend dir
app.mount("/static", StaticFiles(directory=FRONTEND), name="static")
