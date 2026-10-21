"""Streamlit dashboard for the SMA crossover paper trading bot.

Run with:  streamlit run app.py
"""
from datetime import datetime, timedelta, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from bot.backtest import buy_and_hold, max_drawdown, run_backtest
from bot.config import Settings
from bot.data import DemoData, local_market_is_open, make_feed
from bot.engine import run_tick
from bot.paper import PaperAccount

st.set_page_config(page_title="SMA Crossover Bot", page_icon="📈", layout="wide")

settings = Settings()


@st.cache_resource
def get_account(db_path: str, starting_cash: float, slippage: float) -> PaperAccount:
    return PaperAccount(db_path, starting_cash, slippage)


account = get_account(settings.db_path, settings.starting_cash, settings.slippage_pct)
feed = make_feed(settings)

# ---- Sidebar controls ----
st.sidebar.title("⚙️ Bot settings")
symbols = st.sidebar.multiselect(
    "Watchlist (ETFs + Nasdaq)", options=sorted(set(settings.watchlist + list(DemoData.BASE))),
    default=settings.watchlist,
)
extra = st.sidebar.text_input("Add symbols (comma separated)")
symbols += [s.strip().upper() for s in extra.split(",") if s.strip() and s.strip().upper() not in symbols]

preset = st.sidebar.radio("SMA preset", ["20 / 50", "50 / 200 (golden cross)", "Custom"])
if preset == "20 / 50":
    short, long = 20, 50
elif preset.startswith("50 / 200"):
    short, long = 50, 200
else:
    short = st.sidebar.number_input("Short SMA", 2, 500, settings.short_window)
    long = st.sidebar.number_input("Long SMA", 3, 1000, settings.long_window)
if short >= long:
    st.sidebar.error("Short SMA must be smaller than long SMA.")
    st.stop()

running = st.sidebar.toggle("▶️ Bot running", value=False)
interval = st.sidebar.select_slider("Check every (seconds)", [15, 30, 60, 120, 300], value=60)

with st.sidebar.expander("Reset paper account"):
    new_cash = st.number_input("Starting cash ($)", 100.0, 10_000_000.0, settings.starting_cash, 500.0)
    if st.button("Reset", type="primary"):
        account.reset(new_cash)
        st.success("Account reset.")

# ---- Header ----
st.title("📈 SMA Crossover Paper Trading Bot")
st.caption(f"Buy when SMA{short} crosses above SMA{long}, sell when it crosses back below · "
           f"{settings.timeframe} bars · paper money only")
if not settings.has_alpaca_keys:
    st.warning("No Alpaca keys found — showing **demo data** (random prices). "
               "Add your keys to `.env` to use live market data.", icon="🧪")


def market_open() -> bool:
    try:
        return feed.market_is_open()
    except Exception:
        return local_market_is_open()


@st.fragment(run_every=interval if running else None)
def live_panel():
    if not symbols:
        st.info("Pick at least one symbol in the sidebar.")
        return
    is_open = market_open()
    result = run_tick(feed, account, symbols, short, long, is_open, trade=running)

    for err in result.errors:
        st.error(err)
    for t in result.trades:
        st.toast(f"{t['side']} {t['qty']:g} {t['symbol']} @ ${t['price']:.2f}",
                 icon="🟢" if t["side"] == "BUY" else "🔴")

    prices = {s: float(df["close"].iloc[-1]) for s, df in result.bars.items()}
    equity = account.equity(prices)
    pnl = equity - account.starting_cash

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Portfolio value", f"${equity:,.2f}", f"{pnl:+,.2f}")
    c2.metric("Cash", f"${account.cash:,.2f}")
    c3.metric("Open positions", len(account.positions()))
    status = "🟢 Open" if is_open else "🔴 Closed"
    c4.metric("Market", status, "bot running" if running else "bot paused",
              delta_color="normal" if running else "off")
    if running and not is_open:
        st.info("Market is closed — the bot is watching but won't trade until 9:30 ET.", icon="😴")

    # Signals table
    rows = []
    positions = account.positions()
    for sym, df in result.bars.items():
        last = df.iloc[-1]
        pos = positions.get(sym)
        rows.append({
            "Symbol": sym,
            "Price": last["close"],
            f"SMA{short}": last["sma_short"],
            f"SMA{long}": last["sma_long"],
            "Trend": "⬆️ short > long" if last["sma_short"] > last["sma_long"] else "⬇️ short < long",
            "Signal": result.signals.get(sym, "HOLD"),
            "Holding": pos["qty"] if pos else 0,
            "Unrealized P&L": (last["close"] - pos["avg_price"]) * pos["qty"] if pos else 0.0,
        })
    if rows:
        st.subheader("Watchlist")
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                     column_config={c: st.column_config.NumberColumn(format="$%.2f")
                                    for c in ["Price", f"SMA{short}", f"SMA{long}", "Unrealized P&L"]})

    # Chart
    if result.bars:
        st.subheader("Chart")
        sym = st.selectbox("Symbol", list(result.bars), key="chart_symbol")
        df = result.bars[sym]
        fig = go.Figure()
        fig.add_scatter(x=df.index, y=df["close"], name="Price", line=dict(width=1.5))
        fig.add_scatter(x=df.index, y=df["sma_short"], name=f"SMA{short}", line=dict(width=1.5))
        fig.add_scatter(x=df.index, y=df["sma_long"], name=f"SMA{long}", line=dict(width=1.5))
        trades = [t for t in account.trades() if t["symbol"] == sym]
        for side, color, marker in [("BUY", "#16a34a", "triangle-up"), ("SELL", "#dc2626", "triangle-down")]:
            pts = [t for t in trades if t["side"] == side]
            if pts:
                fig.add_scatter(x=pd.to_datetime([t["ts"] for t in pts]), y=[t["price"] for t in pts],
                                mode="markers", name=side,
                                marker=dict(symbol=marker, size=13, color=color))
        fig.update_layout(height=450, margin=dict(l=10, r=10, t=10, b=10),
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
        st.caption("No trades yet. Turn the bot on and wait for a crossover.")


PRESETS = {"20 / 50": (20, 50), "50 / 200": (50, 200)}
BENCHMARK = "QQQ"


@st.cache_data(ttl=3600, show_spinner="Downloading price history...")
def load_history(symbols: tuple[str, ...], fetch_start: datetime, live: bool) -> dict[str, pd.DataFrame]:
    # `live` is only part of the cache key, so demo and real data never mix.
    return feed.get_history(list(symbols), fetch_start, "1Day")


def pct(x: float) -> str:
    return f"{x:+.1f}%"


def backtest_panel():
    st.caption("Replays the exact same rules on **daily** bars: long only, one position per symbol, "
               "cash split evenly, fills at the close plus slippage. Benchmark: buy and hold QQQ.")
    c1, c2 = st.columns([2, 1])
    years = c1.slider("Years of history", 1, 8, 5)
    cash = c2.number_input("Starting cash ($)", 1000.0, 10_000_000.0, settings.starting_cash, 1000.0,
                           key="bt_cash")
    if not symbols:
        st.info("Pick at least one symbol in the sidebar.")
        return
    if st.button("🧪 Run backtest", type="primary"):
        st.session_state.bt_ran = True
    if not st.session_state.get("bt_ran"):
        return

    start = (datetime.now(timezone.utc) - timedelta(days=int(years * 365.25))).replace(
        hour=0, minute=0, second=0, microsecond=0)
    longest = max([long] + [lw for _, lw in PRESETS.values()])
    fetch_start = start - timedelta(days=int(longest * 1.6) + 10)  # SMA warm-up
    try:
        bars = load_history(tuple(sorted(set(symbols) | {BENCHMARK})), fetch_start,
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

    # Run the selected settings plus the presets so they can be compared.
    combos = {f"{short} / {long} (selected)": (short, long)}
    for name, (sw, lw) in PRESETS.items():
        if (sw, lw) != (short, long):
            combos[name] = (sw, lw)
    results = {name: run_backtest(traded, sw, lw, start, cash, settings.slippage_pct)
               for name, (sw, lw) in combos.items()}
    main = results[next(iter(combos))]

    bench = buy_and_hold(bars[BENCHMARK]["close"], start, cash) if BENCHMARK in bars else pd.Series()
    bench_ret = (bench.iloc[-1] / cash - 1) * 100 if not bench.empty else 0.0

    m = main.metrics
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Strategy return", pct(m["total_return_pct"]), f"{m['total_return_pct'] - bench_ret:+.1f} pts vs QQQ")
    k2.metric("Buy & hold QQQ", pct(bench_ret))
    k3.metric("Yearly (CAGR)", pct(m["cagr_pct"]))
    k4.metric("Worst drop", f"{m['max_drawdown_pct']:.1f}%",
              f"QQQ {max_drawdown(bench):.1f}%" if not bench.empty else None, delta_color="off")
    k5.metric("Win rate", f"{m['win_rate_pct']:.0f}%", f"{m['closed_trades']} closed trades", delta_color="off")

    if m["total_return_pct"] > bench_ret:
        st.success(f"The strategy beat buy-and-hold QQQ by {m['total_return_pct'] - bench_ret:.1f} points. "
                   "Check the worst drop too before getting hyped.", icon="🏆")
    else:
        st.warning(f"Buy-and-hold QQQ beat the strategy by {bench_ret - m['total_return_pct']:.1f} points.",
                   icon="💀")

    fig = go.Figure()
    for name, res in results.items():
        fig.add_scatter(x=res.equity.index, y=res.equity, name=f"SMA {name}", line=dict(width=2))
    if not bench.empty:
        fig.add_scatter(x=bench.index, y=bench, name="Buy & hold QQQ",
                        line=dict(width=2, dash="dot", color="#6b7280"))
    fig.update_layout(height=420, margin=dict(l=10, r=10, t=10, b=10), yaxis_tickprefix="$",
                      legend=dict(orientation="h", y=1.02, x=0), hovermode="x unified")
    st.plotly_chart(fig, width="stretch")

    st.subheader("Settings compared")
    table = [{"Settings": name, "Return": r.metrics["total_return_pct"], "CAGR": r.metrics["cagr_pct"],
              "Worst drop": r.metrics["max_drawdown_pct"], "Win rate": r.metrics["win_rate_pct"],
              "Trades": r.metrics["trades"], "Final value": r.metrics["final_value"]}
             for name, r in results.items()]
    if not bench.empty:
        table.append({"Settings": "Buy & hold QQQ", "Return": bench_ret,
                      "CAGR": ((bench.iloc[-1] / cash) ** (365.25 / max((bench.index[-1] - bench.index[0]).days, 1)) - 1) * 100,
                      "Worst drop": max_drawdown(bench), "Win rate": None, "Trades": 1,
                      "Final value": float(bench.iloc[-1])})
    pct_col = st.column_config.NumberColumn(format="%.1f%%")
    st.dataframe(pd.DataFrame(table), hide_index=True, width="stretch",
                 column_config={"Return": pct_col, "CAGR": pct_col, "Worst drop": pct_col,
                                "Win rate": st.column_config.NumberColumn(format="%.0f%%"),
                                "Final value": st.column_config.NumberColumn(format="$%.2f")})

    st.subheader("Per-symbol P&L (selected settings)")
    closed = main.trades.dropna(subset=["pnl"])
    if closed.empty:
        st.caption("No closed trades.")
    else:
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
