"""Standalone mean-reversion scalper. Separate process, own magic (990624),
own daily-loss kill-switch. Trades ONLY its configured (exclusive) symbols.
Dry-run unless --live. ASCII-only console output (stdout is piped)."""
from __future__ import annotations

import sys
import time

from .config import SCALPER
from .scalp_signal import ScalpParams, scalp_decision


def _ts():
    return time.strftime("%H:%M:%S")


class Scalper:
    def __init__(self, cfg=SCALPER, dry_run: bool = True):
        self.cfg = cfg
        self.dry_run = dry_run
        self.client = None
        self._params = ScalpParams(cfg.ema_period, cfg.entry_bps, cfg.exit_bps,
                                   cfg.tp_bps, cfg.stop_bps, cfg.time_stop_bars)
        self._entry = {}        # symbol -> (entry_price, bars_held)
        self._realized_pnl = 0.0
        self._halted = False

    def connect(self):
        from .client import MT5Client
        self.client = MT5Client(magic=self.cfg.magic)
        self.client.connect()

    def _open_count(self):
        return len([p for p in self.client.positions() if p.symbol in self.cfg.symbols])

    def _risk_ok(self):
        """Scalper-level gate. Returns False (and flattens+halts) if the daily
        loss limit is breached."""
        if self._halted:
            return False
        if self._realized_pnl <= -abs(self.cfg.daily_loss_limit):
            print(f"[{_ts()}] [scalper] DAILY-LOSS HALT pnl={self._realized_pnl:+,.0f} "
                  f"<= -{self.cfg.daily_loss_limit:,.0f} -- flattening + halting")
            for p in self.client.positions():
                if p.symbol in self.cfg.symbols:
                    if self.dry_run:
                        print(f"   [DRY] scalp flatten {p.symbol}")
                    else:
                        self.client.close_symbol(p.symbol, comment="scalp-halt")
            self._halted = True
            return False
        return True

    def _signed(self, pos):
        # POSITION_TYPE_BUY == 0 -> long(+1); SELL -> short(-1)
        if pos is None or pos.volume == 0:
            return 0
        return 1 if pos.type == 0 else -1

    def tick_once(self):
        """One decision pass over all configured symbols. Read-only on failure."""
        try:
            import MetaTrader5 as mt5
            tf = mt5.TIMEFRAME_M1
        except Exception:
            tf = 1
        if not self._risk_ok():
            return
        for sym in self.cfg.symbols:
            try:
                rates = self.client.bars(sym, tf, max(self._params.ema_period + 2, 30))
                if rates is None or len(rates) < 2:
                    continue
                closes = [float(x) for x in rates["close"]]
                pos = self.client.position_for(sym)
                cur = self._signed(pos)
                entry_price, bars_held = self._entry.get(sym, (0.0, 0))
                if cur != 0:
                    if not entry_price:        # recover after a restart; avoids /0 in scalp_decision
                        entry_price = closes[-1]
                    bars_held += 1
                    self._entry[sym] = (entry_price, bars_held)
                target, reason = scalp_decision(closes, cur, entry_price, bars_held, self._params)
                if target == cur:
                    continue
                self._apply(sym, cur, target, closes[-1], reason)
            except Exception as e:
                print(f"[{_ts()}] [scalper] {sym} error: {e}")

    def _apply(self, sym, cur, target, price, reason):
        lots = self.cfg.lots.get(sym, 0.0)
        if target == 0:                       # close
            if self.dry_run:
                print(f"   [DRY] scalp CLOSE {sym} ({reason})"); self._entry.pop(sym, None); return
            self.client.close_symbol(sym, comment="scalp")
            self._entry.pop(sym, None)
        elif cur == 0:                        # open (respect max_concurrent)
            if self._open_count() >= self.cfg.max_concurrent:
                return
            if lots <= 0:
                return
            if self.dry_run:
                print(f"   [DRY] scalp {'BUY' if target>0 else 'SELL'} {lots} {sym} ({reason})")
                self._entry[sym] = (price, 0); return
            self.client.market_order(sym, lots, target > 0, comment="scalp")
            self._entry[sym] = (price, 0)
        else:                                 # cur!=0 and target flips: netting -> close first
            if self.dry_run:
                print(f"   [DRY] scalp FLIP-CLOSE {sym} ({reason})"); self._entry.pop(sym, None); return
            self.client.close_symbol(sym, comment="scalp-flip")
            self._entry.pop(sym, None)

    def run(self):
        self.connect()
        mode = "DRY-RUN" if self.dry_run else "LIVE"
        print(f"[{_ts()}] [scalper] {mode} | symbols={self.cfg.symbols} magic={self.cfg.magic}")
        while True:
            self.tick_once()
            time.sleep(self.cfg.poll_secs)


def main():
    if not SCALPER.enabled:
        print("[scalper] ScalperConfig.enabled is False -- nothing to do. "
              "Enable it in bot/config.py after the backtest gate passes.")
        return
    live = "--live" in sys.argv
    Scalper(SCALPER, dry_run=not live).run()


if __name__ == "__main__":
    main()
