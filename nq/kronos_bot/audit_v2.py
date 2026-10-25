"""Audits for the v2 evaluation (no strategy runs, no rule changes).

    python -m nq.kronos_bot.audit_v2 --cfd FILE

1. Fast evaluator vs NautilusTrader on identical signals: the existing guard-free H3 bot run on
   DEV (results/dev_bot_r_to_1530_trades.csv, made before v2 with the same fills and costs) is
   compared trade by trade with what the fast evaluator books for the same days, in index points
   per contract.
2. Data quality: regular sessions with usable 15:30 / 16:00 / 10:00 / 15:45 candles vs NYSE
   trading days, by year.
"""
import argparse

import numpy as np
import pandas as pd

from nq.kronos_bot.research import COST_PTS, NY, OUT, sessions

MULT = 2.0      # MNQ $ per point


def evaluator_vs_bot(cfd_path: str) -> tuple[pd.DataFrame, str]:
    from nq.data import load_mt_cfd
    s = sessions(load_mt_cfd(cfd_path))
    t = pd.read_csv(f"{OUT}/dev_bot_r_to_1530_trades.csv", parse_dates=["entry_ts", "exit_ts"])
    t["date"] = t["entry_ts"].dt.tz_convert(NY).dt.tz_localize(None).dt.normalize()
    t["sign"] = np.where(t["side"] == "LONG", 1, -1)
    j = t.join(s, on="date", how="inner")
    j["fast_sig"] = np.sign(j["r_to_1530"])
    j["fast_net"] = j["fast_sig"] * (j["16:00"] - j["15:30"]) - COST_PTS
    j["bot_net"] = j["pnl"] / j["qty"] / MULT
    j["entry_cost"] = (j["entry"] - j["15:30"]) * j["sign"]          # paid at entry vs the 15:30 close
    j["exit_cost"] = (j["16:00"] - j["exit"]) * j["sign"]            # given up at exit vs the 16:00 close
    stop = j["exit_kind"] == "stop"
    lines = [f"matched trades: {len(j)} of {len(t)} bot trades; same direction: {(j['fast_sig'] == j['sign']).mean() * 100:.1f}%",
             f"mean net points/contract: fast {j['fast_net'].mean():+.3f}, Nautilus {j['bot_net'].mean():+.3f}, "
             f"difference {(j['bot_net'] - j['fast_net']).mean():+.3f}",
             f"entry vs 15:30 close: mean {j['entry_cost'].mean():+.3f} pts (fast books 0.25 in its cost)",
             f"market exits vs 16:00 close: mean {j.loc[~stop, 'exit_cost'].mean():+.3f} pts over {(~stop).sum()} trades",
             f"stop exits: {stop.sum()} ({stop.mean() * 100:.1f}%); their Nautilus net {j.loc[stop, 'bot_net'].mean():+.2f} "
             f"vs fast (held to 16:00) {j.loc[stop, 'fast_net'].mean():+.2f} pts/contract",
             f"correlation of per-trade net (fast vs Nautilus): {j[['fast_net', 'bot_net']].corr().iloc[0, 1]:.3f}",
             "Nautilus results are contract-weighted by risk sizing; the fast evaluator is unit-notional, so R and % "
             "returns differ in scale even when per-contract points agree."]
    return j, "\n".join(lines)


def data_quality(cfd_path: str) -> pd.DataFrame:
    import pandas_market_calendars as mcal
    from nq.data import load_mt_cfd
    f = load_mt_cfd(cfd_path)
    loc = f.index.tz_convert(NY)
    have = pd.DataFrame({"day": loc.normalize().tz_localize(None), "hhmm": loc.strftime("%H:%M")})
    days = mcal.get_calendar("XNYS").schedule(start_date="2016-11-15", end_date=loc[-1].date())
    full = days[days["market_close"].dt.tz_convert(NY).dt.strftime("%H:%M") == "16:00"].index
    rows = []
    for y in sorted(set(full.year)):
        dy = full[full.year == y]
        row = {"year": y, "nyse_full_days": len(dy)}
        for c in ("10:00", "15:30", "15:45", "16:00"):
            row[f"has_{c}"] = int(have[have["hhmm"] == c]["day"].isin(dy).sum())
        row["candles_per_day_median"] = float(have[have["day"].isin(dy)].groupby("day").size().median())
        rows.append(row)
    return pd.DataFrame(rows).set_index("year")


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--cfd", required=True)
    a = p.parse_args(argv)
    j, text = evaluator_vs_bot(a.cfd)
    dq = data_quality(a.cfd)
    j.to_csv(f"{OUT}/v2_audit_fast_vs_nautilus_H3_dev.csv", index=False)
    dq.to_csv(f"{OUT}/v2_audit_data_quality.csv")
    with open(f"{OUT}/v2_audit.txt", "w") as f:
        f.write("== fast evaluator vs NautilusTrader (H3, DEV, guard-free)\n" + text + "\n\n== data quality\n" + dq.to_string() + "\n")
    print(text)
    print(dq.to_string())


if __name__ == "__main__":
    main()
