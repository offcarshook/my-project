"""Streamlit dashboard for the paper trading bot.

Run with:  streamlit run app.py
"""
from datetime import datetime, timedelta, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from bot.backtest import buy_and_hold, max_drawdown, run_backtest, run_challenges
from bot.book import FAILED, PASSED, ChallengeRules, RiskConfig
from bot.config import Settings
from bot.data import TF_MINUTES, DemoData, local_market_is_open, make_feed
from bot.engine import run_tick
from bot.paper import PaperAccount
from bot.strategy import SmaCross, VwapPullback

st.set_page_config(page_title="Prop Trading Bot", page_icon="📈", layout="wide")

VWAP, SMA = VwapPullback.name, SmaCross.name
MODES = {
    "🎯 VWAP Pullback": dict(strategy=VWAP, timeframe="5Min", leverage=4.0, risk_pct=0.5, stop_pct=0.0,
                            take_profit_pct=0.0, reward_risk=2.0, max_trades=10, allow_short=True,
                            max_positions=3, flatten_eod=True, challenge=True, interval=30),
    "⚡ SMA Scalper": dict(strategy=SMA, timeframe="1Min", short=5, long=20, leverage=4.0, risk_pct=0.5,
                          stop_pct=0.4, take_profit_pct=0.0, reward_risk=2.0, max_trades=10,
                          allow_short=True, max_positions=2, flatten_eod=True, challenge=True, interval=15),
    "🐢 Safe": dict(strategy=SMA, timeframe="15Min", short=20, long=50, leverage=1.0, risk_pct=0.0,
                   stop_pct=0.0, take_profit_pct=0.0, reward_risk=0.0, max_trades=0, allow_short=False,
                   max_positions=0, flatten_eod=False, challenge=False, interval=60),
}
MAX_RISK_PCT = 1.0
TIMEFRAMES = ["1Min", "5Min", "15Min", "30Min", "1Hour", "1Day"]
BENCHMARK = "QQQ"

settings = Settings()


@st.cache_resource
def get_account(db_path: str, starting_cash: float) -> PaperAccount:
    return PaperAccount(db_path, starting_cash)


account = get_account(settings.db_path, settings.starting_cash)

# ---- Sidebar controls ----
st.sidebar.title("⚙️ Bot settings")
mode = st.sidebar.radio("Mode", list(MODES), horizontal=True)
d = MODES[mode]

symbols = st.sidebar.multiselect(
    "Watchlist (ETFs + Nasdaq)", options=sorted(set(settings.watchlist + list(DemoData.BASE))),
    default=settings.watchlist,
)
extra = st.sidebar.text_input("Add symbols (comma separated)")
symbols += [s.strip().upper() for s in extra.split(",") if s.strip() and s.strip().upper() not in symbols]

with st.sidebar.expander("📊 Strategy", expanded=True):
    st.markdown(f"**{d['strategy']}**")
    timeframe = st.selectbox("Bar size", TIMEFRAMES, index=TIMEFRAMES.index(d["timeframe"]),
                             key=f"tf_{mode}")
    if d["strategy"] == VWAP:
        c1, c2 = st.columns(2)
        fast = c1.number_input("Fast EMA", 2, 100, 9, key=f"fast_{mode}")
        slow = c2.number_input("Slow EMA", 3, 300, 21, key=f"slow_{mode}")
        rvol = st.slider("Min volume vs average", 0.0, 3.0, 1.2, 0.1, key=f"rvol_{mode}",
                         help="Higher = pickier, fewer trades. 1.2 = bar volume at least 20% above normal")
        if fast >= slow:
            st.error("Fast EMA must be smaller than slow EMA.")
            st.stop()
        strategy = VwapPullback(fast, slow, rvol_min=rvol)
    else:
        c1, c2 = st.columns(2)
        short = c1.number_input("Short SMA", 2, 500, d["short"], key=f"short_{mode}")
        long = c2.number_input("Long SMA", 3, 1000, d["long"], key=f"long_{mode}")
        if short >= long:
            st.error("Short SMA must be smaller than long SMA.")
            st.stop()
        strategy = SmaCross(short, long)

with st.sidebar.expander("⚡ Risk", expanded=mode != "🐢 Safe"):
    risk_pct = st.slider("Risk per trade (% of account)", 0.0, MAX_RISK_PCT, d["risk_pct"], 0.1,
                         key=f"risk_{mode}", help="Capped at 1%. 0 = split cash evenly across the watchlist")
    if d["strategy"] == VWAP:
        st.caption("Stops come from the strategy (just past the pullback, 0.5–2 ATR).")
        stop_pct = 0.0
    else:
        stop_pct = st.slider("Stop loss %", 0.0, 5.0, d["stop_pct"], 0.05, key=f"stop_{mode}")
    reward_risk = st.slider("Target (x the risk)", 0.0, 5.0, d["reward_risk"], 0.25, key=f"rr_{mode}",
                            help="2 = aim to win twice what you risk. 0 = no target")
    max_trades = st.number_input("Max new trades per day (0 = no limit)", 0, 100, d["max_trades"],
                                 key=f"maxtr_{mode}")
    max_positions = st.number_input("Max open positions (0 = no limit)", 0, 20, d["max_positions"],
                                    key=f"maxpos_{mode}")
    leverage = st.slider("Leverage cap", 1.0, 10.0, d["leverage"], 0.5, key=f"lev_{mode}")
    allow_short = st.toggle("Short on sell signals", d["allow_short"], key=f"short_on_{mode}")
    flatten_eod = st.toggle("Close everything before 4pm", d["flatten_eod"], key=f"eod_{mode}")
    slippage = st.number_input("Trading cost per side %", 0.0, 1.0, settings.slippage_pct, 0.01,
                               format="%.2f", help="Spread + slippage + fees, paid on entry and exit")

with st.sidebar.expander("🏆 Prop challenge rules", expanded=d["challenge"]):
    challenge_on = st.toggle("Enforce challenge rules", d["challenge"], key=f"ch_{mode}")
    target = st.number_input("Profit target %", 1.0, 100.0, 8.0, 0.5)
    daily_loss = st.number_input("Max daily loss %", 0.5, 50.0, 5.0, 0.5)
    max_dd = st.number_input("Max total loss %", 1.0, 90.0, 10.0, 0.5)
    window = st.number_input("Days to pass", 5, 120, 30)

risk = RiskConfig(leverage=leverage, risk_pct=risk_pct, stop_pct=stop_pct, reward_risk=reward_risk,
                  max_trades_per_day=int(max_trades), allow_short=allow_short,
                  max_positions=int(max_positions), flatten_eod=flatten_eod, slippage_pct=slippage)
rules = ChallengeRules(challenge_on, target, daily_loss, max_dd)
intraday = timeframe != "1Day"

settings.timeframe = timeframe
feed = make_feed(settings)

running = st.sidebar.toggle("▶️ Bot running", value=False)
interval = st.sidebar.select_slider("Check every (seconds)", [10, 15, 30, 60, 120, 300],
                                    value=d["interval"], key=f"interval_{mode}")

with st.sidebar.expander("Reset paper account"):
    new_cash = st.number_input("Starting cash ($)", 100.0, 10_000_000.0, settings.starting_cash, 500.0)
    if st.button("Reset", type="primary"):
        account.reset(new_cash)
        st.success("Account reset.")

# ---- Header ----
st.title("📈 Prop Challenge Paper Trading Bot")
st.caption(f"{mode} · {strategy.name} on {timeframe} bars · {risk_pct:g}% risk per trade · "
           f"{'longs + shorts' if allow_short else 'longs only'} · paper money only")
if not settings.has_alpaca_keys:
    st.warning("No Alpaca keys found — showing **demo data** (random prices). "
               "Add your keys to `.env` to use live market data.", icon="🧪")
if risk_pct and stop_pct and stop_pct < 10 * slippage:
    st.caption(f"⚠️ A {stop_pct:g}% stop is tight next to {2 * slippage:g}% of trading costs "
               "per round trip. Fees eat a big chunk of every win at this speed.")


def market_open() -> bool:
    try:
        return feed.market_is_open()
    except Exception:
        return local_market_is_open()


def money(x: float) -> str:
    return f"-${-x:,.2f}" if x < 0 else f"${x:,.2f}"


def challenge_panel(book, equity: float):
    start = book.starting_cash
    day_start = book.day_start_equity or start
    days = (datetime.now(timezone.utc) - account.started_at).days
    status = {PASSED: "🏆 PASSED", FAILED: f"💀 FAILED ({book.status_reason})"}.get(book.status, "⏳ In progress")
    st.subheader(f"Prop challenge: {status}")
    c1, c2, c3, c4 = st.columns(4)
    gain = (equity / start - 1) * 100
    c1.metric("Progress to target", f"{gain:+.2f}%", f"target +{target:g}%", delta_color="off")
    c1.progress(min(max(gain / target, 0.0), 1.0))
    today = min((equity / day_start - 1) * 100, 0)
    c2.metric("Today's loss", f"{today:.2f}%", f"limit -{daily_loss:g}%", delta_color="off")
    c2.progress(min(-today / daily_loss, 1.0))
    total = min(gain, 0)
    c3.metric("Total loss", f"{total:.2f}%", f"limit -{max_dd:g}%", delta_color="off")
    c3.progress(min(-total / max_dd, 1.0))
    c4.metric("Day", f"{days + 1} / {window}")
    c4.progress(min((days + 1) / window, 1.0))
    if book.status != "ACTIVE":
        st.info("The challenge is over. Reset the paper account in the sidebar to start a new one.")


@st.fragment(run_every=interval if running else None)
def live_panel():
    if not symbols:
        st.info("Pick at least one symbol in the sidebar.")
        return
    is_open = market_open()
    result = run_tick(feed, account, symbols, strategy, is_open, risk, rules, timeframe, trade=running)

    for err in result.errors:
        st.error(err)
    for t in result.trades:
        st.toast(f"{t['side']} {t['qty']:g} {t['symbol']} @ ${t['price']:.2f} ({t['reason']})",
                 icon="🟢" if t["side"] in ("BUY", "COVER") else "🔴")

    book = result.book
    equity = book.equity(result.prices)
    exposure = book.gross_exposure(result.prices)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Portfolio value", money(equity), f"{equity - book.starting_cash:+,.2f}")
    c2.metric("Exposure", money(exposure), f"{exposure / equity:.1f}x of equity" if equity > 0 else None,
              delta_color="off")
    c3.metric("Open positions", len(book.positions),
              f"{book.trades_today} trades today" + (f" / {max_trades} max" if max_trades else ""),
              delta_color="off")
    c4.metric("Market", "🟢 Open" if is_open else "🔴 Closed", "bot running" if running else "bot paused",
              delta_color="normal" if running else "off")
    if running and not is_open:
        st.info("Market is closed — the bot is watching but won't trade until 9:30 ET.", icon="😴")

    if challenge_on:
        challenge_panel(book, equity)

    rows = []
    for sym, df in result.bars.items():
        last = df.iloc[-1]
        pos = book.positions.get(sym)
        price = result.prices.get(sym, last["close"])
        cols = list(strategy.lines)
        rows.append({
            "Symbol": sym,
            "Price": price,
            **{strategy.lines[c]: last[c] for c in cols},
            "Trend": "⬆️ up" if last[cols[-2]] > last[cols[-1]] else "⬇️ down",
            "Signal": result.signals.get(sym, "HOLD"),
            "Position": ("🟢 LONG " if pos.qty > 0 else "🔴 SHORT ") + f"{abs(pos.qty):g}" if pos else "—",
            "Entry": pos.entry if pos else None,
            "Stop": pos.stop if pos else None,
            "Target": pos.target if pos else None,
            "Unrealized P&L": (price - pos.entry) * pos.qty if pos else 0.0,
        })
    if rows:
        st.subheader("Watchlist")
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                     column_config={c: st.column_config.NumberColumn(format="$%.2f")
                                    for c in ["Price", *strategy.lines.values(), "Entry", "Stop",
                                              "Target", "Unrealized P&L"]})

    if result.bars:
        st.subheader("Chart")
        sym = st.selectbox("Symbol", list(result.bars), key="chart_symbol")
        df = result.bars[sym]
        fig = go.Figure()
        if {"open", "high", "low"} <= set(df.columns):
            fig.add_candlestick(x=df.index, open=df["open"], high=df["high"], low=df["low"],
                                close=df["close"], name="Price")
        else:
            fig.add_scatter(x=df.index, y=df["close"], name="Price", line=dict(width=1.5))
        for col, label in strategy.lines.items():
            fig.add_scatter(x=df.index, y=df[col], name=label, line=dict(width=1.5))
        trades = [t for t in account.trades() if t["symbol"] == sym]
        for sides, color, marker, name in [(("BUY", "COVER"), "#16a34a", "triangle-up", "Buy / cover"),
                                           (("SELL", "SHORT"), "#dc2626", "triangle-down", "Sell / short")]:
            pts = [t for t in trades if t["side"] in sides]
            if pts:
                fig.add_scatter(x=pd.to_datetime([t["ts"] for t in pts], utc=True),
                                y=[t["price"] for t in pts], mode="markers", name=name,
                                marker=dict(symbol=marker, size=13, color=color))
        fig.update_layout(height=450, margin=dict(l=10, r=10, t=10, b=10), xaxis_rangeslider_visible=False,
                          legend=dict(orientation="h", y=1.02, x=0), hovermode="x unified")
        st.plotly_chart(fig, width="stretch")

    st.subheader("Trade log")
    trades = account.trades()
    if trades:
        log = pd.DataFrame(trades).drop(columns=["id"])
        st.dataframe(log, hide_index=True, width="stretch",
                     column_config={"price": st.column_config.NumberColumn(format="$%.2f"),
                                    "pnl": st.column_config.NumberColumn("realized P&L", format="$%.2f")})
    else:
        st.caption("No trades yet. Turn the bot on and wait for a setup.")


@st.cache_data(ttl=3600, show_spinner="Downloading price history...")
def load_history(symbols: tuple[str, ...], fetch_start: datetime, timeframe: str,
                 live: bool) -> dict[str, pd.DataFrame]:
    # `live` is only part of the cache key, so demo and real data never mix.
    return feed.get_history(list(symbols), fetch_start, timeframe)


def pct(x: float) -> str:
    return f"{x:+.1f}%"


def backtest_panel():
    st.caption(f"Replays the exact rules from the sidebar ({mode}, {timeframe} bars) on past prices. "
               "Stops and targets trigger on each bar's high/low and fill at their level, so a gap "
               "through a stop is filled better than it would be for real.")
    c1, c2 = st.columns([2, 1])
    # 1-minute history is huge, so keep those backtests shorter.
    max_months, default_months = {"1Day": (96, 60), "1Min": (6, 2)}.get(timeframe, (24, 6))
    months = c1.slider("Months of history", 1, max_months, default_months, key=f"bt_months_{timeframe}")
    cash = c2.number_input("Starting cash ($)", 1000.0, 10_000_000.0, settings.starting_cash, 1000.0,
                           key="bt_cash")
    if not symbols:
        st.info("Pick at least one symbol in the sidebar.")
        return
    if st.button("🧪 Run backtest", type="primary"):
        st.session_state.bt_ran = True
    if not st.session_state.get("bt_ran"):
        return

    start = (datetime.now(timezone.utc) - timedelta(days=int(months * 30.44))).replace(
        hour=0, minute=0, second=0, microsecond=0)
    bars_per_day = 1 if not intraday else max(390 // TF_MINUTES[timeframe], 1)
    warmup_days = int(strategy.warmup / bars_per_day * 1.6) + 10
    fetch_start = start - timedelta(days=warmup_days)
    try:
        bars = load_history(tuple(sorted(set(symbols) | {BENCHMARK})), fetch_start, timeframe,
                            settings.has_alpaca_keys)
    except Exception as exc:
        st.error(f"Data fetch failed: {exc}")
        return
    missing = [s for s in symbols if bars.get(s) is None or bars[s].empty]
    if missing:
        st.warning(f"No history for: {', '.join(missing)}")
    traded = {s: bars[s] for s in symbols if s not in missing}
    if not traded:
        return

    with st.spinner("Simulating..."):
        main = run_backtest(traded, strategy, start, cash, risk, intraday)
        challenges = run_challenges(traded, strategy, start, cash, risk, rules, intraday,
                                    window_days=int(window), every_n_days=1 if months <= 12 else 5)

    bench = buy_and_hold(bars[BENCHMARK]["close"], start, cash) if BENCHMARK in bars else pd.Series()
    bench_ret = (bench.iloc[-1] / cash - 1) * 100 if not bench.empty else 0.0
    m = main.metrics

    # ---- Prop challenge simulator ----
    st.subheader(f"🏆 Prop challenge simulator: +{target:g}% in {window} days, "
                 f"max -{daily_loss:g}%/day, -{max_dd:g}% total")
    if challenges.empty:
        st.info(f"Not enough history for a {window}-day challenge. Pick more months.")
    else:
        n = len(challenges)
        counts = challenges["result"].value_counts()
        passed = challenges[challenges["result"] == "PASSED"]
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Pass rate", f"{counts.get('PASSED', 0) / n * 100:.0f}%", f"{n} challenges simulated",
                  delta_color="off")
        k2.metric("Blown accounts", f"{counts.get('FAILED', 0) / n * 100:.0f}%")
        k3.metric("Ran out of time", f"{counts.get('TIMEOUT', 0) / n * 100:.0f}%")
        k4.metric("Avg days to pass", f"{passed['days'].mean():.0f}" if not passed.empty else "—")
        rate = counts.get("PASSED", 0) / n
        if rate >= 0.5:
            st.success("Passes more often than not on this data. Now check it on months it wasn't tuned on.",
                       icon="🏆")
        elif rate > 0:
            st.warning(f"Passes about 1 in {round(1 / rate)} tries. Budget for the challenge fees.", icon="⚠️")
        else:
            st.error("Never passed on this data. Tweak the settings or pick another strategy.", icon="💀")

        colors = {"PASSED": "#16a34a", "FAILED": "#dc2626", "TIMEOUT": "#9ca3af"}
        fig = go.Figure()
        for res, color in colors.items():
            part = challenges[challenges["result"] == res]
            if not part.empty:
                fig.add_bar(x=part["start"], y=part["return_pct"], name=res, marker_color=color)
        fig.add_hline(y=target, line_dash="dot", line_color="#16a34a")
        fig.add_hline(y=-max_dd, line_dash="dot", line_color="#dc2626")
        fig.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10), yaxis_ticksuffix="%",
                          legend=dict(orientation="h", y=1.05, x=0), xaxis_title="Challenge start date",
                          yaxis_title="Result")
        st.plotly_chart(fig, width="stretch")
        with st.expander("Why challenges ended"):
            st.dataframe(challenges.groupby(["result", "reason"]).size().rename("count").reset_index(),
                         hide_index=True, width="stretch")

    # ---- Full-period backtest ----
    st.subheader("📈 Whole period (no challenge rules)")
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Strategy return", pct(m["total_return_pct"]),
              f"{m['total_return_pct'] - bench_ret:+.1f} pts vs QQQ")
    k2.metric("Avg month", pct(m["monthly_pct"]))
    k3.metric("Trades per day", f"{m['trades_per_day']:.1f}")
    k4.metric("Worst drop", f"{m['max_drawdown_pct']:.1f}%",
              f"QQQ {max_drawdown(bench):.1f}%" if not bench.empty else None, delta_color="off")
    breakeven = 100 / (1 + reward_risk) if reward_risk else None
    k5.metric("Win rate", f"{m['win_rate_pct']:.0f}%",
              f"need >{breakeven:.0f}% before fees" if breakeven else f"{m['closed_trades']} closed trades",
              delta_color="off")

    fig = go.Figure()
    fig.add_scatter(x=main.equity.index, y=main.equity, name=f"{mode}", line=dict(width=2))
    if not bench.empty:
        fig.add_scatter(x=bench.index, y=bench, name="Buy & hold QQQ",
                        line=dict(width=2, dash="dot", color="#6b7280"))
    fig.update_layout(height=380, margin=dict(l=10, r=10, t=10, b=10), yaxis_tickprefix="$",
                      legend=dict(orientation="h", y=1.02, x=0), hovermode="x unified")
    st.plotly_chart(fig, width="stretch")

    if not main.equity.empty:
        monthly = main.equity.resample("ME").last()
        monthly = monthly.pct_change().fillna(monthly.iloc[0] / cash - 1) * 100
        fig = go.Figure(go.Bar(x=monthly.index, y=monthly,
                               marker_color=["#16a34a" if v >= 0 else "#dc2626" for v in monthly]))
        fig.update_layout(height=260, margin=dict(l=10, r=10, t=30, b=10), yaxis_ticksuffix="%",
                          title=f"Monthly returns · {int((monthly > 0).sum())} green / "
                                f"{int((monthly <= 0).sum())} red months")
        st.plotly_chart(fig, width="stretch")

    closed = main.trades.dropna(subset=["pnl"])
    if not closed.empty:
        st.subheader("Per-symbol P&L")
        per = closed.groupby("symbol")["pnl"].agg(["sum", "count", lambda p: (p > 0).mean() * 100])
        per.columns = ["Realized P&L", "Trades", "Win rate"]
        st.dataframe(per.sort_values("Realized P&L", ascending=False), width="stretch",
                     column_config={"Realized P&L": st.column_config.NumberColumn(format="$%.2f"),
                                    "Win rate": st.column_config.NumberColumn(format="%.0f%%")})

    with st.expander(f"All {len(main.trades)} trades"):
        st.dataframe(main.trades, hide_index=True, width="stretch",
                     column_config={"price": st.column_config.NumberColumn(format="$%.2f"),
                                    "pnl": st.column_config.NumberColumn(format="$%.2f")})


live_tab, backtest_tab = st.tabs(["🤖 Live bot", "🧪 Backtest"])
with live_tab:
    live_panel()
with backtest_tab:
    backtest_panel()
