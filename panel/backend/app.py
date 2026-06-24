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

# overridable in tests; default to the repo's live dirs
LOG_DIR = os.environ.get("PANEL_LOG_DIR", "logs")
STATE_DIR = os.environ.get("PANEL_STATE_DIR", "state")

_HERE = os.path.dirname(os.path.abspath(__file__))
FRONTEND = os.path.join(_HERE, "..", "frontend")

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
    acct = last["account"]
    return {
        "ts": last["ts"], "mode": last.get("mode"),
        "account": acct,
        "gauges": data.risk_gauges(acct),
        "tripwires": data.tripwire_status(acct),
        "consensus": data.consensus_rows(last),
        "regime": last.get("regime", {}),
        "models": last.get("models", {}),
        "cost": last.get("cost", {}),
        "orders": last.get("orders", []),
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
def bars(symbol: str):
    """Recent bars + each model's projection cone for one symbol. Needs live
    MT5; degrades to {available:false} otherwise."""
    c = _mt5()
    if c is None:
        return {"available": False}
    try:
        import MetaTrader5 as mt5
        rates = c.bars(symbol, mt5.TIMEFRAME_M15, 120)
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


@app.get("/")
def index():
    return FileResponse(os.path.join(FRONTEND, "index.html"))


# static assets (styles.css, app.js) served from the frontend dir
app.mount("/static", StaticFiles(directory=FRONTEND), name="static")
