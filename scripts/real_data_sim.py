"""Run the bot's backtest and prop-challenge simulator on real index history.

Uses the daily NASDAQ Composite and S&P 500 candles (1999-2018) that ship inside the
`arch` package, so it works without API keys or internet:

    pip install arch
    python scripts/real_data_sim.py

Daily candles only: the intraday VWAP pullback strategy needs Alpaca 5-minute data instead.
"""
import sys
from pathlib import Path

import pandas as pd
from arch.data import nasdaq, sp500

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bot.backtest import buy_and_hold, max_drawdown, run_backtest, run_challenges  # noqa: E402
from bot.book import ChallengeRules, RiskConfig  # noqa: E402
from bot.strategy import SmaCross  # noqa: E402

START, CASH, COST = "1999-12-01", 10_000, 0.05


def load() -> dict[str, pd.DataFrame]:
    out = {}
    for label, module in [("NASDAQ", nasdaq), ("S&P500", sp500)]:
        df = module.load().rename(columns=str.lower)
        out[label] = df[["open", "high", "low", "close", "volume"]]
    return out


def main():
    bars = load()
    print(f"=== Whole period {START[:7]} to 2018-12, long only, no leverage, {COST}% cost per side ===")
    for label, df in bars.items():
        bh = buy_and_hold(df["close"], START, CASH)
        print(f"{label:7} buy & hold: {(bh.iloc[-1] / CASH - 1) * 100:+7.1f}%  worst drop {max_drawdown(bh):6.1f}%")
        for short, long in [(20, 50), (50, 200)]:
            m = run_backtest({label: df}, SmaCross(short, long), START, CASH, RiskConfig(slippage_pct=COST)).metrics
            print(f"{label:7} SMA {short}/{long:<4}: {m['total_return_pct']:+7.1f}%  worst drop "
                  f"{m['max_drawdown_pct']:6.1f}%  win {m['win_rate_pct']:3.0f}%  trades {m['closed_trades']}")

    print("\n=== Prop challenge: +8% in 30 days, -5%/day, -10% total, a new one every 5 trading days ===")
    rules = ChallengeRules(profit_target_pct=8, max_daily_loss_pct=5, max_drawdown_pct=10)
    configs = {
        "SMA 20/50, longs only, 1x": (SmaCross(20, 50), RiskConfig(slippage_pct=COST)),
        "SMA 20/50, long+short, 0.5% risk, 2% stop, 2R": (
            SmaCross(20, 50), RiskConfig(leverage=4, risk_pct=0.5, stop_pct=2, reward_risk=2,
                                         allow_short=True, slippage_pct=COST)),
        "SMA 5/20, long+short, 0.5% risk, 1.5% stop, 2R": (
            SmaCross(5, 20), RiskConfig(leverage=4, risk_pct=0.5, stop_pct=1.5, reward_risk=2,
                                        allow_short=True, slippage_pct=COST)),
        "SMA 5/20, long+short, 1% risk, 1.5% stop, 2R": (
            SmaCross(5, 20), RiskConfig(leverage=4, risk_pct=1.0, stop_pct=1.5, reward_risk=2,
                                        allow_short=True, slippage_pct=COST)),
    }
    for name, (strategy, risk) in configs.items():
        out = run_challenges(bars, strategy, START, CASH, risk, rules, window_days=30, every_n_days=5)
        share = out["result"].value_counts(normalize=True) * 100
        m = run_backtest(bars, strategy, START, CASH, risk).metrics
        print(f"{name}\n   {len(out)} challenges: pass {share.get('PASSED', 0):.1f}%, "
              f"fail {share.get('FAILED', 0):.1f}%, out of time {share.get('TIMEOUT', 0):.1f}%"
              f"  |  whole period {m['total_return_pct']:+.1f}%, {m['trades_per_day']:.2f} trades/day")


if __name__ == "__main__":
    main()
