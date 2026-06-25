"""The integrated scalper SLOT (engine-hosted, own magic 990624 for attribution).
The Engine constructs one Scalper and calls step() each loop; there is no
standalone process, so two order streams can never race on the netting account.

Phase 2 fills the slot with a crypto FAST-MOMENTUM strategy: decisions run
PER ENGINE TICK (~5s, not per closed bar) on the latest price, alts are gated by
a BTC lead-lag confirmation, and exits are server-side (a broker take-profit AND
stop-loss attached at entry) so a 5-10s trade captures its P&L regardless of loop
speed. The decision function is injectable so the slot mechanics stay testable
independent of the strategy. Account-wide risk (kill-switch, new-risk gating) is
the Engine's job; the slot keeps its own broker-deal-sourced daily-loss halt and
stays dry-run until the operator arms it.
"""
from __future__ import annotations

import time

from .config import SCALPER


def _ts():
    return time.strftime("%H:%M:%S")


def _m1_tf():
    try:
        import MetaTrader5 as mt5
        return mt5.TIMEFRAME_M1
    except Exception:
        return 1


class Scalper:
    def __init__(self, cfg=SCALPER, client=None, dry_run: bool = True, decide=None):
        self.cfg = cfg
        self.dry_run = dry_run
        self.client = client
        self._decide = decide or self._crypto_decide   # (sym, closes, cur, btc_closes)->(target,reason)
        self._realized_pnl = 0.0
        self._halted = False
        self._last_action = {}   # symbol -> epoch of last open/flip (anti-churn cooldown)
        self._trades = {}        # symbol -> [open timestamps] (rolling-hour turnover cap)
        self._opened_at = {}     # symbol -> epoch of last open (min-hold)
        self._state = {}         # symbol -> last per-symbol status (for the panel)
        self.live_symbols = None      # set by engine: symbols cleared to trade LIVE
        self.params_by_symbol = {}    # set by engine: gate-locked params per symbol
        self.state_dir = None         # set by engine: where to write scalper.json

    def _p(self, sym):
        """Effective strategy params for `sym`: the gate-locked params if the
        engine armed it, else the config defaults (spec §3.4)."""
        g = self.params_by_symbol.get(sym) or {}
        return (int(g.get("lookback", self.cfg.lookback)),
                float(g.get("entry", self.cfg.entry_bps)),
                float(g.get("tp", self.cfg.tp_bps)),
                float(g.get("stop", self.cfg.stop_bps)))

    def _is_dry(self, sym):
        """A symbol trades live only if the engine put it in live_symbols
        (None = no per-symbol gate -> follow the global dry_run)."""
        if self.dry_run:
            return True
        return self.live_symbols is not None and sym not in self.live_symbols

    # ---- lifecycle -----------------------------------------------------
    def connect(self):
        """Build this strategy's own magic-tagged client; shares the one global
        MT5 session already opened by the engine's client."""
        from .client import MT5Client
        self.client = MT5Client(magic=self.cfg.magic)
        self.client.connect()

    # ---- default strategy: crypto fast-momentum ------------------------
    def _crypto_decide(self, sym, closes, cur, btc_closes):
        from .crypto_signal import crypto_decision, CryptoParams
        lookback, entry_bps, _tp, _stop = self._p(sym)
        p = CryptoParams(lookback=lookback, entry_bps=entry_bps,
                         lead_lookback=self.cfg.lead_lookback, lead_bps=self.cfg.lead_bps)
        return crypto_decision(closes, cur, btc_closes=btc_closes, p=p)

    # ---- helpers -------------------------------------------------------
    def _open_count(self):
        return len([p for p in self.client.positions() if p.symbol in self.cfg.symbols])

    def _signed(self, pos):
        if pos is None or pos.volume == 0:
            return 0
        return 1 if pos.type == 0 else -1   # 0=buy -> long(+1), else short(-1)

    def _foreign_position(self, sym):
        """True if a position in `sym` exists under a DIFFERENT magic (e.g. the AI
        engine) — never open into it on a netting account. Defensive; the symbol
        partition should already prevent overlap."""
        try:
            allpos = self.client.positions(magic=None)
        except Exception:
            return False
        return any(p.symbol == sym and getattr(p, "magic", self.cfg.magic) != self.cfg.magic
                   for p in allpos)

    def _digits(self, sym):
        try:
            import MetaTrader5 as mt5
            info = mt5.symbol_info(sym)
            return info.digits if info else 5
        except Exception:
            return 5

    def _sl_tp_for(self, sym, is_buy, ref_price):
        """Broker stop (`stop_bps` adverse) and take-profit (`tp_bps` favorable)
        from `ref_price` — server-side fast exits. Both are clamped to the broker's
        minimum stop distance (spec C2) so the tight bps levels aren't rejected.
        Returns (sl, tp); 0.0 for either if uncomputable."""
        if ref_price <= 0:
            return 0.0, 0.0
        _lb, _e, tp_bps, stop_bps = self._p(sym)
        sbps, tbps = stop_bps / 1e4, tp_bps / 1e4
        if is_buy:
            sl = self.client.clamp_stop_price(sym, ref_price, ref_price * (1 - sbps), "below")
            tp = self.client.clamp_stop_price(sym, ref_price, ref_price * (1 + tbps), "above")
        else:
            sl = self.client.clamp_stop_price(sym, ref_price, ref_price * (1 + sbps), "above")
            tp = self.client.clamp_stop_price(sym, ref_price, ref_price * (1 - tbps), "below")
        return sl, tp

    def _protect(self, sym, is_buy):
        """Attach server-side SL+TP to the just-opened position, anchored to the
        ACTUAL fill price (M1). Check the retcode and retry once; if a valid STOP
        still can't be attached, CLOSE the position — a naked few-second scalp is
        worse than no trade (C1)."""
        pos = self.client.position_for(sym)
        ref = float(getattr(pos, "price_open", 0.0)) if pos else 0.0
        sl, tp = self._sl_tp_for(sym, is_buy, ref)
        if sl:
            ok, _ = self.client.set_sltp(sym, sl, tp)
            if not ok:
                ok, _ = self.client.set_sltp(sym, sl, tp)   # one retry on a fresh attempt
            if ok:
                return
        print(f"[{_ts()}] [scalper] !! {sym} could not attach a stop -- closing position")
        self.client.close_symbol(sym, comment="scalp-nostop")

    def _ensure_stops(self):
        """Backstop sweep: any of THIS scalper's LIVE positions missing a broker
        stop gets one (or is closed). The engine's _ensure_stops is magic-filtered
        and cannot see scalper-magic positions, so the scalper guards its own (C1).
        Per-position try/except so one bad symbol never stalls the engine tick."""
        for p in self.client.positions():               # own magic
            if (p.symbol not in self.cfg.symbols or self._is_dry(p.symbol)
                    or getattr(p, "sl", 0.0)):
                continue
            try:
                self._protect(p.symbol, p.type == 0)
            except Exception as e:
                print(f"[{_ts()}] [scalper] ensure-stop {p.symbol} failed: {e}")

    def _flatten_own(self, comment):
        for p in self.client.positions():
            if p.symbol in self.cfg.symbols:
                if self.dry_run:
                    print(f"   [DRY] scalp flatten {p.symbol}")
                else:
                    self.client.close_symbol(p.symbol, comment=comment)

    def _realized(self):
        """Today's realized P&L for this magic, from BROKER deal history so it
        counts broker SL/TP closes too (B2). Falls back to self-accumulated."""
        try:
            return self.client.realized_profit(magic=self.cfg.magic, days=1)
        except Exception:
            return self._realized_pnl

    def _risk_ok(self):
        if self._halted:
            return False
        pnl = self._realized()
        if pnl <= -abs(self.cfg.daily_loss_limit):
            print(f"[{_ts()}] [scalper] DAILY-LOSS HALT pnl={pnl:+,.0f} "
                  f"<= -{self.cfg.daily_loss_limit:,.0f} -- flattening + halting")
            self._flatten_own("scalp-halt")
            self._halted = True
            return False
        return True

    def _bars(self, sym, n):
        rates = self.client.bars(sym, _m1_tf(), n)
        if rates is None or len(rates) < 2:
            return None
        return [float(x) for x in rates["close"]]

    # ---- per-tick decision pass ---------------------------------------
    def step(self, allow_new_risk: bool = True):
        """One decision pass over assigned symbols, every engine tick (~5s).
        `allow_new_risk` mirrors the engine's gate: when False the slot may
        close/reduce but not open."""
        if self.client is None or not self._risk_ok():
            self._write_state(); return
        self._ensure_stops()   # backstop: no scalper position runs naked
        # BTC closes once per pass for the alt lead-lag tilt
        btc = None
        if any(s != "BTCUSD" for s in self.cfg.symbols):
            btc = self._bars("BTCUSD", max(self.cfg.lead_lookback + 2, 30))
        for sym in self.cfg.symbols:
            try:
                self._step_symbol(sym, btc, allow_new_risk)
            except Exception as e:
                print(f"[{_ts()}] [scalper] {sym} error: {e}")
        self._write_state()

    def _step_symbol(self, sym, btc, allow_new_risk):
        closes = self._bars(sym, max(self.cfg.lookback + 2, 30))   # latest incl forming bar
        if closes is None:
            return
        pos = self.client.position_for(sym)
        cur = self._signed(pos)
        btc_closes = btc if sym != "BTCUSD" else None
        target, reason = self._decide(sym, closes, cur, btc_closes)
        self._capture(sym, pos, cur, target, reason)
        if target != cur:
            self._apply(sym, cur, target, closes[-1], reason,
                        profit=float(getattr(pos, "profit", 0.0)),
                        allow_new_risk=allow_new_risk)

    def _capture(self, sym, pos, cur, target, reason):
        """Snapshot what the scalper is doing for `sym` (for the panel)."""
        now = time.time()
        cd = max(0.0, self.cfg.cooldown_secs - (now - self._last_action.get(sym, 0.0)))
        if self._halted:               state = "halted"
        elif self._is_dry(sym):        state = "dry-run"
        elif pos:                      state = "in_position"
        elif cd > 0:                   state = "cooldown"
        else:                          state = "watching"
        self._state[sym] = {
            "symbol": sym, "state": state, "live": not self._is_dry(sym),
            "position": ({"dir": cur, "lots": abs(pos.volume),
                          "profit": round(float(getattr(pos, "profit", 0.0)), 2)} if pos else None),
            "last_signal": {"target": target, "reason": reason},
            "cooldown_s": round(cd, 1),
            "trades_last_hour": self._trades_last_hour(sym, now),
        }

    def _write_state(self):
        """Atomically emit state/scalper.json for the panel (spec §6.2)."""
        if not self.state_dir:
            return
        try:
            import os
            from .controls import atomic_write_json
            for s in list(self._state):           # drop symbols no longer assigned
                if s not in self.cfg.symbols:
                    self._state.pop(s, None)
            obj = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "halted": self._halted,
                   "realized_today": round(self._realized(), 2),
                   "symbols": [self._state.get(s, {"symbol": s, "state": "—"})
                               for s in self.cfg.symbols]}
            atomic_write_json(os.path.join(self.state_dir, "scalper.json"), obj)
        except Exception as e:
            print(f"[{_ts()}] [scalper] state write failed: {e}")

    def _lots_for(self, sym):
        """Lots for `sym` from the per-symbol USD risk budget (spec §5). 0.0 means
        not sized -> the open is skipped and surfaced (never a silent no-op)."""
        try:
            return self.client.lots_for_notional(sym, self.cfg.notional_per_symbol)
        except Exception:
            return 0.0

    def _trades_last_hour(self, sym, now):
        recent = [t for t in self._trades.get(sym, []) if now - t < 3600.0]
        self._trades[sym] = recent
        return len(recent)

    def _record_open(self, sym, now):
        self._last_action[sym] = now
        self._trades.setdefault(sym, []).append(now)
        self._opened_at[sym] = now

    def _held_long_enough(self, sym, now):
        """Min-hold gate: the scalper won't close/flip a position it opened less
        than min_hold_secs ago (broker TP/SL still exit early, server-side)."""
        return now - self._opened_at.get(sym, 0.0) >= self.cfg.min_hold_secs

    def _apply(self, sym, cur, target, price, reason, *, profit=0.0, allow_new_risk=True):
        lots = self._lots_for(sym)
        dry = self._is_dry(sym)               # global OR not gate-cleared for live
        now = time.time()
        if target == 0:                       # exit (rare: usually broker TP/SL exits)
            if not self._held_long_enough(sym, now):
                return                        # min-hold; let broker TP/SL exit early
            if dry:
                print(f"   [DRY] scalp CLOSE {sym} ({reason})"); return
            ok, _ = self.client.close_symbol(sym, comment="scalp")
            if ok:
                self._realized_pnl += profit
        elif cur == 0:                        # open
            if not allow_new_risk:
                return
            if now - self._last_action.get(sym, 0.0) < self.cfg.cooldown_secs:
                return                        # anti-churn cooldown
            if self._foreign_position(sym):
                print(f"[{_ts()}] [scalper] {sym} held by another system -- standing down")
                return
            if lots <= 0:
                print(f"[{_ts()}] [scalper] {sym} not sized (budget -> 0 lots) -- skipping")
                return
            if self._open_count() >= self.cfg.max_concurrent:
                return
            if self._trades_last_hour(sym, now) >= self.cfg.max_trades_per_hour:
                print(f"[{_ts()}] [scalper] {sym} trade cap {self.cfg.max_trades_per_hour}/hr -- skipping")
                return
            if dry:
                print(f"   [DRY] scalp {'BUY' if target>0 else 'SELL'} {lots} {sym} ({reason})")
                self._record_open(sym, now); return
            # Open WITHOUT inline SL/TP (this broker rejects DEALs carrying them,
            # ret 10006), then attach server-side exits via the SLTP action.
            ok, _ = self.client.market_order(sym, lots, target > 0, comment="scalp")
            if ok:
                self._record_open(sym, now)
                self._protect(sym, target > 0)
        else:                                 # reverse-breakout flip: close (re-open next tick)
            if now - self._last_action.get(sym, 0.0) < self.cfg.cooldown_secs:
                return                        # anti-churn cooldown
            if not self._held_long_enough(sym, now):
                return                        # min-hold before a flip
            if dry:
                print(f"   [DRY] scalp FLIP-CLOSE {sym} ({reason})")
                self._last_action[sym] = now; return
            ok, _ = self.client.close_symbol(sym, comment="scalp-flip")
            if ok:
                self._realized_pnl += profit
                self._last_action[sym] = now
