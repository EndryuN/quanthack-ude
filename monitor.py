"""Read-only live dashboard. Run in a second terminal while the engine trades.
Shows equity, the competition metrics, risk posture, tripwire watches, and
open positions. Sends nothing."""
import time

from bot.client import MT5Client
from bot.risk import RiskEngine
from bot.state import StateTracker
from bot.config import CONFIG, INITIAL_EQUITY


def main():
    c = MT5Client(magic=CONFIG.magic)
    c.connect()
    risk = RiskEngine(c, CONFIG.risk)
    st = StateTracker(INITIAL_EQUITY, state_dir=CONFIG.state_dir)
    try:
        while True:
            a = c.account()
            snap = risk.assess()
            m = st.update(a.equity)
            print("\033[2J\033[H", end="")  # clear screen
            print("=" * 64)
            print(f" QUANTHACK MONITOR   {time.strftime('%Y-%m-%d %H:%M:%S')}")
            print("=" * 64)
            print(f" Equity     {a.equity:>14,.2f} USD   Balance {a.balance:>12,.2f}")
            print(f" Return     {m.return_pct*100:>+13.3f}%   (vs 1,000,000 start)")
            print(f" Max DD     {m.max_drawdown*100:>13.2f}%   Cur DD {m.cur_drawdown*100:>8.2f}%")
            shc = "  (capped@50: <8 obs)" if st.n_return_obs() < 8 else ""
            print(f" Sharpe15m  {m.sharpe_15m:>13.3f}    obs={st.n_return_obs()}{shc}")
            print("-" * 64)
            print(f" Gross lev  {snap.gross_leverage:>12.2f}x    Margin use {snap.margin_usage*100:>6.1f}%")
            print(f" Margin lvl {a.margin_level:>12.0f}%    Free margin {a.margin_free:>12,.0f}")
            print(f" Net dir    {snap.net_directional_frac*100:>11.0f}%    Top {snap.largest_symbol or '-'} "
                  f"{snap.largest_frac*100:.0f}%")
            print(f" New risk   {'ALLOWED' if snap.allow_new_risk else 'BLOCKED':>12}    "
                  f"KILL {'YES' if snap.kill else 'no':>4}")
            if snap.reasons:
                print("-" * 64)
                for r in snap.reasons:
                    print(f"  ! {r}")
            print("-" * 64)
            positions = c.positions()
            if positions:
                print(f" {'SYMBOL':8} {'SIDE':4} {'LOTS':>8} {'OPEN':>12} {'NOW':>12} {'P/L':>12}")
                for p in sorted(positions, key=lambda x: -abs(x.profit)):
                    side = "BUY" if p.type == 0 else "SELL"
                    print(f" {p.symbol:8} {side:4} {p.volume:>8.2f} {p.price_open:>12.5f} "
                          f"{p.price_current:>12.5f} {p.profit:>+12.2f}")
            else:
                print(" (no open positions)")
            print("=" * 64)
            time.sleep(CONFIG.loop_seconds)
    except KeyboardInterrupt:
        pass
    finally:
        c.shutdown()


if __name__ == "__main__":
    main()
