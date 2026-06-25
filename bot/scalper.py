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
        p = CryptoParams(lookback=self.cfg.lookback, entry_bps=self.cfg.entry_bps,
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
        from `ref_price` — server-side fast exits. (sl, tp); 0.0 if uncomputable."""
        if ref_price <= 0:
            return 0.0, 0.0
        sbps, tbps, digits = self.cfg.stop_bps / 1e4, self.cfg.tp_bps / 1e4, self._digits(sym)
        if is_buy:
            return round(ref_price * (1 - sbps), digits), round(ref_price * (1 + tbps), digits)
        return round(ref_price * (1 + sbps), digits), round(ref_price * (1 - tbps), digits)

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
            return
        # BTC closes once per pass for the alt lead-lag tilt
        btc = None
        if any(s != "BTCUSD" for s in self.cfg.symbols):
            btc = self._bars("BTCUSD", max(self.cfg.lead_lookback + 2, 30))
        for sym in self.cfg.symbols:
            try:
                self._step_symbol(sym, btc, allow_new_risk)
            except Exception as e:
                print(f"[{_ts()}] [scalper] {sym} error: {e}")

    def _step_symbol(self, sym, btc, allow_new_risk):
        closes = self._bars(sym, max(self.cfg.lookback + 2, 30))   # latest incl forming bar
        if closes is None:
            return
        pos = self.client.position_for(sym)
        cur = self._signed(pos)
        btc_closes = btc if sym != "BTCUSD" else None
        target, reason = self._decide(sym, closes, cur, btc_closes)
        if target != cur:
            self._apply(sym, cur, target, closes[-1], reason,
                        profit=float(getattr(pos, "profit", 0.0)),
                        allow_new_risk=allow_new_risk)

    def _apply(self, sym, cur, target, price, reason, *, profit=0.0, allow_new_risk=True):
        lots = self.cfg.lots.get(sym, 0.0)
        if target == 0:                       # exit (rare: usually broker TP/SL exits)
            if self.dry_run:
                print(f"   [DRY] scalp CLOSE {sym} ({reason})"); return
            ok, _ = self.client.close_symbol(sym, comment="scalp")
            if ok:
                self._realized_pnl += profit
        elif cur == 0:                        # open
            if not allow_new_risk:
                return
            if time.time() - self._last_action.get(sym, 0.0) < self.cfg.cooldown_secs:
                return                        # anti-churn cooldown
            if self._foreign_position(sym):
                print(f"[{_ts()}] [scalper] {sym} held by another system -- standing down")
                return
            if self._open_count() >= self.cfg.max_concurrent or lots <= 0:
                return
            if self.dry_run:
                print(f"   [DRY] scalp {'BUY' if target>0 else 'SELL'} {lots} {sym} ({reason})")
                self._last_action[sym] = time.time(); return
            # Open WITHOUT inline SL/TP (this broker rejects DEALs carrying them,
            # ret 10006), then attach server-side exits via the SLTP action.
            ok, _ = self.client.market_order(sym, lots, target > 0, comment="scalp")
            if ok:
                self._last_action[sym] = time.time()
                sl, tp = self._sl_tp_for(sym, target > 0, price)
                if sl or tp:
                    self.client.set_sltp(sym, sl, tp)
                else:
                    print(f"[{_ts()}] [scalper] !! {sym} opened without TP/SL -- check quotes")
        else:                                 # reverse-breakout flip: close (re-open next tick)
            if time.time() - self._last_action.get(sym, 0.0) < self.cfg.cooldown_secs:
                return                        # anti-churn cooldown
            if self.dry_run:
                print(f"   [DRY] scalp FLIP-CLOSE {sym} ({reason})")
                self._last_action[sym] = time.time(); return
            ok, _ = self.client.close_symbol(sym, comment="scalp-flip")
            if ok:
                self._realized_pnl += profit
                self._last_action[sym] = time.time()
