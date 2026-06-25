"""Mean-reversion scalper, integrated into the engine process (own magic 990624
for P&L attribution only). The Engine constructs one Scalper and calls step()
each loop; there is NO standalone process anymore, so two order streams can never
race on the netting account (scalper integration spec, D1/D5).

Decisions are made ONCE PER CLOSED M1 BAR (D2) so live behavior matches the
backtest. The scalper owns its symbols fully (D3): it computes the target net
direction via the fade logic, executes through its own (magic-tagged) client with
a tight broker stop, and the engine excludes its symbols from the AI's sizing /
trailing / stop logic. Account-wide risk (kill-switch, new-risk gating) is the
Engine's job; the scalper keeps its own (now functional) daily-loss halt.
"""
from __future__ import annotations

import time

from .config import SCALPER
from .scalp_signal import ScalpParams, scalp_decision


def _ts():
    return time.strftime("%H:%M:%S")


def _m1_tf():
    try:
        import MetaTrader5 as mt5
        return mt5.TIMEFRAME_M1
    except Exception:
        return 1


def _forming_bar_ts(rates):
    """Timestamp of the latest (still-forming) M1 bar, or None if unavailable.
    A change in this value means a new bar opened -> the prior bar just CLOSED."""
    try:
        return int(rates["time"][-1])
    except Exception:
        return None


class Scalper:
    def __init__(self, cfg=SCALPER, client=None, dry_run: bool = True):
        self.cfg = cfg
        self.dry_run = dry_run
        self.client = client
        self._params = ScalpParams(cfg.ema_period, cfg.entry_bps, cfg.exit_bps,
                                   cfg.tp_bps, cfg.stop_bps, cfg.time_stop_bars)
        self._bars_held = {}     # symbol -> bars the current position has been held
        self._last_bar_ts = {}   # symbol -> last-seen forming-bar timestamp
        self._realized_pnl = 0.0
        self._halted = False

    # ---- lifecycle -----------------------------------------------------
    def connect(self):
        """Build this strategy's own magic-tagged client. Shares the one global
        MT5 session already opened by the engine's client."""
        from .client import MT5Client
        self.client = MT5Client(magic=self.cfg.magic)
        self.client.connect()

    # ---- helpers -------------------------------------------------------
    def _open_count(self):
        return len([p for p in self.client.positions() if p.symbol in self.cfg.symbols])

    def _signed(self, pos):
        if pos is None or pos.volume == 0:
            return 0
        return 1 if pos.type == 0 else -1   # 0=buy -> long(+1), else short(-1)

    def _foreign_position(self, sym):
        """True if a position in `sym` exists under a DIFFERENT magic (e.g. the AI
        engine). On a netting account we must never open into it. Defensive — the
        symbol partition should already prevent overlap."""
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
        from `ref_price`. Both fill server-side at the level — the scalper's fast
        exits (spec §9.3). Returns (sl, tp); 0.0 for either if not computable."""
        if ref_price <= 0:
            return 0.0, 0.0
        sbps = self.cfg.stop_bps / 1e4
        tbps = self.cfg.tp_bps / 1e4
        digits = self._digits(sym)
        if is_buy:
            sl = round(ref_price * (1 - sbps), digits)
            tp = round(ref_price * (1 + tbps), digits)
        else:
            sl = round(ref_price * (1 + sbps), digits)
            tp = round(ref_price * (1 - tbps), digits)
        return sl, tp

    def _flatten_own(self, comment):
        for p in self.client.positions():
            if p.symbol in self.cfg.symbols:
                if self.dry_run:
                    print(f"   [DRY] scalp flatten {p.symbol}")
                else:
                    self.client.close_symbol(p.symbol, comment=comment)

    def _realized(self):
        """Today's realized P&L for this magic, sourced from BROKER deal history so
        it counts broker-side SL/TP closes too (B2). Falls back to the
        self-accumulated figure if deal history is unavailable."""
        try:
            return self.client.realized_profit(magic=self.cfg.magic, days=1)
        except Exception:
            return self._realized_pnl

    def _risk_ok(self):
        """Scalper-level gate: the daily realized-loss halt. Account-wide
        protection is the engine's kill-switch."""
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

    # ---- per-bar decision pass ----------------------------------------
    def step(self, allow_new_risk: bool = True):
        """One decision pass over configured symbols, acting only on NEWLY CLOSED
        M1 bars. Called by the engine each loop. `allow_new_risk` mirrors the
        engine's risk gate: when False the scalper may close/reduce but not open."""
        if self.client is None or not self._risk_ok():
            return
        tf = _m1_tf()
        for sym in self.cfg.symbols:
            try:
                self._step_symbol(sym, tf, allow_new_risk)
            except Exception as e:
                print(f"[{_ts()}] [scalper] {sym} error: {e}")

    def _step_symbol(self, sym, tf, allow_new_risk):
        rates = self.client.bars(sym, tf, max(self._params.ema_period + 2, 30))
        if rates is None or len(rates) < 3:
            return
        bar_ts = _forming_bar_ts(rates)
        if bar_ts is None:
            return   # fail closed (B6): can't determine bar cadence -> don't act
        prev_ts = self._last_bar_ts.get(sym)
        # act only when a new bar has closed (the forming-bar timestamp advanced)
        if bar_ts == prev_ts:
            return
        new_bar = prev_ts is not None
        self._last_bar_ts[sym] = bar_ts
        # decide on COMPLETED bars only (drop the still-forming last bar)
        closes = [float(x) for x in rates["close"]][:-1]
        if len(closes) < 2:
            return
        pos = self.client.position_for(sym)
        cur = self._signed(pos)
        # entry basis is the ACTUAL fill (broker position), robust across restarts
        entry_price = float(getattr(pos, "price_open", 0.0)) if pos else 0.0
        if cur != 0:
            if new_bar:
                self._bars_held[sym] = self._bars_held.get(sym, 0) + 1
        else:
            self._bars_held.pop(sym, None)
        bars_held = self._bars_held.get(sym, 0)
        target, reason = scalp_decision(closes, cur, entry_price, bars_held, self._params)
        if target != cur:
            self._apply(sym, cur, target, closes[-1], reason,
                        profit=float(getattr(pos, "profit", 0.0)),
                        allow_new_risk=allow_new_risk)

    def _apply(self, sym, cur, target, price, reason, *, profit=0.0, allow_new_risk=True):
        lots = self.cfg.lots.get(sym, 0.0)
        if target == 0:                       # exit
            if self.dry_run:
                print(f"   [DRY] scalp CLOSE {sym} ({reason})")
                self._bars_held.pop(sym, None); return
            ok, _ = self.client.close_symbol(sym, comment="scalp")
            if ok:
                self._realized_pnl += profit
                self._bars_held.pop(sym, None)
        elif cur == 0:                        # open
            if not allow_new_risk:
                return                         # engine blocks new risk
            if self._foreign_position(sym):
                print(f"[{_ts()}] [scalper] {sym} held by another system -- standing down")
                return
            if self._open_count() >= self.cfg.max_concurrent or lots <= 0:
                return
            if self.dry_run:
                print(f"   [DRY] scalp {'BUY' if target>0 else 'SELL'} {lots} {sym} ({reason})")
                self._bars_held[sym] = 0; return
            sl, tp = self._sl_tp_for(sym, target > 0, price)
            ok, _ = self.client.market_order(sym, lots, target > 0, comment="scalp",
                                             sl=sl, tp=tp)
            self._bars_held[sym] = 0
            # B4: never leave a scalp open without its server-side TP/SL. If the
            # order went through but a leg was missing (no quote pre-trade), retry
            # via set_sltp; warn loudly if it still can't attach.
            if ok and (not sl or not tp):
                sl2, tp2 = self._sl_tp_for(sym, target > 0, price)
                if sl2 or tp2:
                    self.client.set_sltp(sym, sl2 or sl, tp2 or tp)
                    sl, tp = sl2 or sl, tp2 or tp
                if not sl or not tp:
                    print(f"[{_ts()}] [scalper] !! {sym} opened without "
                          f"{'SL' if not sl else 'TP'} -- check quotes")
        else:                                 # flip (signal logic never flips; guard anyway)
            if self.dry_run:
                print(f"   [DRY] scalp FLIP-CLOSE {sym} ({reason})")
                self._bars_held.pop(sym, None); return
            ok, _ = self.client.close_symbol(sym, comment="scalp-flip")
            if ok:
                self._realized_pnl += profit
                self._bars_held.pop(sym, None)
