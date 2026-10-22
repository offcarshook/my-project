"""Prop Pass dashboard: live status of the paper bot and the latest backtest report.

Run with:  streamlit run app.py   (the bot itself runs with `python propbot.py paper`)
"""
import json
from pathlib import Path

import pandas as pd
import streamlit as st

from bot.config import ConfigError, load_config

st.set_page_config(page_title="Prop Pass", page_icon="🏆", layout="wide")
st.title("🏆 Prop Pass")

try:
    cfg = load_config("config.yaml")
except ConfigError as exc:
    st.error(f"config.yaml problem: {exc}")
    st.stop()

c = cfg.challenge
st.caption(f"EMA {cfg.strategy.fast}/{cfg.strategy.slow} on {cfg.strategy.timeframe} candles · "
           f"{cfg.risk.risk_per_trade_pct}% risk per trade · challenge +{c.profit_target_pct}% / "
           f"-{c.max_daily_loss_pct}% daily / -{c.max_total_loss_pct}% total ({c.drawdown_type}) · paper trading only")


def money(x: float) -> str:
    return f"-${-x:,.2f}" if x < 0 else f"${x:,.2f}"


@st.fragment(run_every=15)
def paper_status():
    path = Path(cfg.paper.state_dir) / "status.json"
    if not path.exists():
        st.info("The paper bot hasn't written a status yet. Start it with `python propbot.py paper`.")
        return
    s = json.loads(path.read_text())
    if s.get("locked"):
        st.error(f"🔒 Locked: {s.get('reason')}. Run `python propbot.py reset --yes` to start a new challenge.")
    elif s.get("challenge_status") == "PASSED":
        st.success("🏆 Profit target reached. Trading stopped.")
    if "equity" in s:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Equity", money(s["equity"]), f"{s['pnl_total']:+,.2f} total")
        c2.metric("Today", f"{s['pnl_today']:+,.2f}",
                  f"{s['daily_loss_used_pct']:.2f}% of {s['daily_limit_pct']:g}% daily limit used", delta_color="off")
        c3.metric("Overall drawdown", f"{s['overall_drawdown_pct']:.2f}%",
                  f"{money(s['distance_to_total_floor'])} to the floor", delta_color="off")
        c4.metric("Open exposure", money(s["open_exposure"]), f"{len(s['positions'])} positions", delta_color="off")
        st.progress(min(max(s["pnl_total"] / (s["target"] - s["initial"]), 0.0), 1.0),
                    text=f"Progress to target {money(s['target'])}")
    st.caption(f"{s['note']} · {s['trades_today']} trades today · updated {s['time']} (refreshes every 15 s)")
    if s["positions"]:
        st.dataframe(pd.DataFrame(s["positions"]).T, width="stretch")
    logs = sorted(Path(cfg.paper.log_dir).glob("events-*.jsonl"))
    if logs:
        lines = logs[-1].read_text().splitlines()[-100:]
        st.subheader("Recent events")
        st.dataframe(pd.DataFrame([json.loads(x) for x in reversed(lines)]), hide_index=True, width="stretch")


def latest_report():
    reports = sorted(Path("reports").glob("*/report.md"))
    if not reports:
        st.info("No backtest report yet. Run `python propbot.py backtest`.")
        return
    st.caption(f"{reports[-1].parent}")
    st.markdown(reports[-1].read_text())


status_tab, report_tab = st.tabs(["📡 Paper bot", "📊 Latest backtest report"])
with status_tab:
    paper_status()
with report_tab:
    latest_report()
