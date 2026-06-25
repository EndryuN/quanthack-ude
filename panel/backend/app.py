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
    Read-only: we only call account()/positions()/bars()."""
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
        "regime": last.get("regime", {}),
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


@app.get("/api/positions")
def positions():
    c = _mt5()
    if c is None:
        return {"available": False, "positions": []}
    try:
        out = [{"symbol": p.symbol, "lots": p.signed_lots,
                "profit": getattr(p, "profit", 0.0)} for p in c.positions()]
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
        _TF = {"1m": mt5.TIMEFRAME_M1, "15m": mt5.TIMEFRAME_M15, "1h": mt5.TIMEFRAME_H1}
        timeframe = _TF.get(tf, mt5.TIMEFRAME_M15)
        rates = c.bars(symbol, timeframe, 120)
        if rates is None or len(rates) < 20:
            return {"available": False}
        closes = [float(x) for x in rates["close"]]
        times = [int(x) for x in rates["time"]]
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
        return {"available": True, "symbol": symbol, "times": times,
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
              "second_opinion", "exposure_cap"):
        if k in body:
            c[k] = body[k]
    c["apply_seq"] = int(c.get("apply_seq", 0)) + 1   # apply knobs within one tick
    ctrl.atomic_write_json(_controls_path(), c)
    return c


@app.post("/api/flatten")
def post_flatten():
    c = ctrl.load_controls(STATE_DIR)
    c["flatten_seq"] = int(c.get("flatten_seq", 0)) + 1
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
                                   "take_profit": None, "note": ""})
    for k in ("pinned", "view", "take_profit", "note"):
        if k in body:
            entry[k] = body[k]
    o[symbol] = entry
    ctrl.atomic_write_json(_overrides_path(), o)
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
