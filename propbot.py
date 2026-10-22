"""Prop Pass command line.

    python propbot.py backtest            # download/cached Alpaca data -> compare configs -> test -> report
    python propbot.py backtest --synthetic  # pipeline check on random data (NOT performance evidence)
    python propbot.py verify              # check credentials, account, symbols, data and spreads
    python propbot.py paper               # run the paper-trading bot (Ctrl+C stops it safely)
    python propbot.py stop                # EMERGENCY STOP: cancel orders, close positions, lock
    python propbot.py status              # print the running bot's latest status
    python propbot.py reset --yes         # clear a lock and start a fresh challenge
"""
import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from bot.config import ConfigError, Settings, load_config


def setup_logging(log_dir: str):
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout),
                                  logging.FileHandler(Path(log_dir) / "propbot.log")])


def cmd_backtest(cfg, args):
    from bot.data import AlpacaData, DemoData, load_bars
    from bot.http import ApiError
    from bot.pipeline import run_pipeline

    start = pd.Timestamp(cfg.backtest.train[0]) - pd.Timedelta(days=14)   # indicator warm-up
    end = pd.Timestamp(cfg.backtest.test[1])
    if args.synthetic:
        print("⚠️  SYNTHETIC random-walk data: this only checks that the pipeline runs. The numbers mean nothing.")
        bars = DemoData().get_history(cfg.watchlist, start.tz_localize("America/New_York"), cfg.strategy.timeframe)
    else:
        settings = Settings()
        if not settings.has_alpaca_keys:
            sys.exit("ALPACA_API_KEY / ALPACA_SECRET_KEY are not set. Copy .env.example to .env and fill them in, "
                     "or run with --synthetic for a pipeline check.")
        print(f"Loading {cfg.strategy.timeframe} candles {start.date()} → {end.date()} "
              f"({cfg.backtest.data_feed} feed, cached in {cfg.backtest.cache_dir})")
        try:
            bars = load_bars(AlpacaData(settings), cfg.watchlist, start, end, cfg.strategy.timeframe,
                             cfg.backtest.data_feed, cfg.backtest.cache_dir)
        except ApiError as exc:
            if exc.status in (401, 403):
                sys.exit(f"Alpaca refused the data request ({exc}). Check your keys; if your plan can't read "
                         "SIP history, set backtest.data_feed: iex in config.yaml.")
            raise
        empty = [s for s, df in bars.items() if df.empty]
        if empty:
            print(f"No data for: {', '.join(empty)} (left out)")
            bars = {s: df for s, df in bars.items() if not df.empty}
    out = run_pipeline(cfg, bars, out_dir=args.out, synthetic=args.synthetic)
    print(f"\nReport: {out / 'report.md'}")


def cmd_verify(cfg, args):
    from bot.broker import AlpacaPaperBroker
    from bot.http import ApiError

    broker = AlpacaPaperBroker(Settings(), cfg.paper.data_feed)
    acct = broker.account()
    print(f"Paper account {acct.get('account_number')}: status {acct.get('status')}, equity ${float(acct['equity']):,.2f}, "
          f"multiplier {acct.get('multiplier')}, shorting {'on' if acct.get('shorting_enabled') else 'OFF'}, "
          f"pattern day trader {acct.get('pattern_day_trader')}")
    if float(acct["equity"]) < 25_000:
        print("⚠️  Equity under $25,000: US pattern-day-trader rules can block day trades on a margin account.")
    start = datetime.now(timezone.utc) - timedelta(days=5)
    bars = broker.bars(cfg.watchlist, start, cfg.strategy.timeframe)
    print(f"\n{'symbol':6} {'tradable':8} {'shortable':9} {'easy borrow':11} {'candles(5d)':11} spread")
    problems = 0
    for sym in cfg.watchlist:
        try:
            a = broker.asset(sym)
        except ApiError as exc:
            print(f"{sym:6} NOT FOUND ({exc})")
            problems += 1
            continue
        q = broker.latest_quote(sym)
        spread = f"{(q['ask'] - q['bid']) / ((q['ask'] + q['bid']) / 2) * 1e4:.1f} bps" if q and q.get("bid") and q.get("ask") else "n/a"
        n = len(bars.get(sym, []))
        ok = a.get("tradable") and n > 0
        problems += not ok
        print(f"{sym:6} {str(a.get('tradable')):8} {str(a.get('shortable')):9} {str(a.get('easy_to_borrow')):11} {n:<11} {spread}")
    if not cfg.challenge.confirmed_firm_rules:
        print("\nNote: challenge.confirmed_firm_rules is false. Fine for paper practice; before any real challenge, "
              "confirm your firm allows ETFs, leveraged ETFs, shorting and automated trading, and set its exact drawdown rules.")
    print("\nAll good." if not problems else f"\n{problems} problem(s) found.")


def cmd_paper(cfg, args):
    from bot.broker import AlpacaPaperBroker
    from bot.trader import Trader

    broker = AlpacaPaperBroker(Settings(), cfg.paper.data_feed)
    print("Starting Prop Pass paper trading (Alpaca PAPER account). Ctrl+C or `python propbot.py stop` to stop.")
    Trader(cfg, broker).run()


def cmd_stop(cfg, args):
    from bot.trader import emergency_stop

    broker = None
    if Settings().has_alpaca_keys:
        from bot.broker import AlpacaPaperBroker
        broker = AlpacaPaperBroker(Settings(), cfg.paper.data_feed)
    emergency_stop(cfg, broker)
    print("EMERGENCY STOP: kill file written" + (", all orders cancelled and positions closed." if broker else
          " (no credentials here, so the running bot will flatten on its next poll)."))
    print("Trading stays locked until `python propbot.py reset --yes`.")


def cmd_status(cfg, args):
    path = Path(cfg.paper.state_dir) / "status.json"
    if not path.exists():
        sys.exit("No status yet: start the bot with `python propbot.py paper`.")
    print(json.dumps(json.loads(path.read_text()), indent=2))


def cmd_reset(cfg, args):
    from bot.risk import RiskManager

    if not args.yes:
        sys.exit("This clears the lock and starts a new challenge. Re-run with --yes to confirm.")
    state_dir = Path(cfg.paper.state_dir)
    (state_dir / "KILL").unlink(missing_ok=True)
    path = state_dir / "trader_state.json"
    if not path.exists():
        print("Nothing to reset.")
        return
    state = json.loads(path.read_text())
    equity = None
    if Settings().has_alpaca_keys:
        from bot.broker import AlpacaPaperBroker
        equity = float(AlpacaPaperBroker(Settings(), cfg.paper.data_feed).account()["equity"])
    rm = RiskManager.from_dict(cfg.challenge, state["risk"]) if state.get("risk") else RiskManager(cfg.challenge, equity or 0)
    rm.reset(equity)
    state["risk"] = rm.to_dict()
    path.write_text(json.dumps(state, indent=2))
    print(f"Reset done: new challenge starting balance ${rm.state.initial:,.2f}.")


def main(argv=None):
    p = argparse.ArgumentParser(description="Prop Pass: EMA crossover prop-challenge bot (paper trading only)")
    p.add_argument("--config", default="config.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backtest", help="run the backtest pipeline and write a report")
    b.add_argument("--synthetic", action="store_true", help="random data, pipeline check only")
    b.add_argument("--out", default="reports")
    sub.add_parser("verify", help="check account, symbols, data and spreads")
    sub.add_parser("paper", help="run the paper-trading bot")
    sub.add_parser("stop", help="emergency stop")
    sub.add_parser("status", help="show the latest bot status")
    r = sub.add_parser("reset", help="clear a lock and start a new challenge")
    r.add_argument("--yes", action="store_true")
    args = p.parse_args(argv)
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        sys.exit(f"Config problem: {exc}")
    setup_logging(cfg.paper.log_dir)
    {"backtest": cmd_backtest, "verify": cmd_verify, "paper": cmd_paper, "stop": cmd_stop,
     "status": cmd_status, "reset": cmd_reset}[args.cmd](cfg, args)


if __name__ == "__main__":
    main()
