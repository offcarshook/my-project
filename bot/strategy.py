"""Prop Pass strategies. Each one turns completed candles into per-candle columns:

  signal       BUY / SELL / HOLD (entries; executed at the NEXT candle's open)
  cross        +1 / -1 / 0: a raw opposite-direction signal that closes an open position
  atr          ATR used for stops when the strategy doesn't place its own
  stop_dist    the strategy's own stop distance in price (NaN = use the ATR stop from risk.stop)
  target_dist  the strategy's own target distance in price (NaN = risk.reward_risk x stop)

Every value at row i only uses candles up to and including i.
"""
import numpy as np
import pandas as pd

from bot.indicators import NY, atr, efficiency_ratio, ema, htf_close_and_ema, ny_index

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"
EXIT_ANY = 2     # `cross` value that closes a position on either side


def closes_position(cross: int, long: bool) -> bool:
    """Does this candle's `cross` close an open long (or short)?"""
    return cross == EXIT_ANY or cross == (-1 if long else 1)


def stop_distance(price, atr_value, stop) -> float | np.ndarray:
    """Stop distance in price units from a StopConfig: ATR-based (clamped to min/max % of
    price) or a fixed % of price."""
    if stop.type == "fixed":
        return price * stop.fixed_pct / 100
    return np.clip(stop.atr_mult * atr_value, price * stop.min_pct / 100, price * stop.max_pct / 100)


def clamp_stop(price: float, dist: float, stop) -> float:
    """A strategy's own stop, kept between risk.stop.min_pct and max_pct of price."""
    return float(np.clip(dist, price * stop.min_pct / 100, price * stop.max_pct / 100))


# ---------------------------------------------------------------------------
# Shared pieces
# ---------------------------------------------------------------------------
def filter_masks(df: pd.DataFrame, cfg, bar_minutes: int, data_feed: str) -> tuple[pd.Series, pd.Series]:
    """Long/short permission from the optional filters in strategy.filters (adds their inputs
    to df for inspection). Every strategy goes through these."""
    f = cfg.filters
    c = df["close"]
    ok_long = pd.Series(True, index=df.index) & df["atr"].notna()
    ok_short = ok_long.copy()
    if f.htf_trend["enabled"]:
        htf = htf_close_and_ema(df, bar_minutes, f.htf_trend["minutes"], f.htf_trend["ema"])
        df["htf_close"], df["htf_ema"] = htf["htf_close"], htf["htf_ema"]
        ok_long &= df["htf_close"] > df["htf_ema"]
        ok_short &= df["htf_close"] < df["htf_ema"]
    if f.atr_band["enabled"]:
        ratio = df["atr"] / df["atr"].rolling(f.atr_band["lookback_bars"], min_periods=50).median()
        df["atr_ratio"] = ratio
        in_band = ratio.between(f.atr_band["min_ratio"], f.atr_band["max_ratio"])
        ok_long &= in_band
        ok_short &= in_band
    if f.liquidity["enabled"] and "volume" in df:
        floor = f.liquidity["min_dollar_volume_iex" if data_feed == "iex" else "min_dollar_volume_sip"]
        df["dollar_volume"] = c * df["volume"]
        liquid = df["dollar_volume"] >= floor
        ok_long &= liquid
        ok_short &= liquid
    if f.chop["enabled"]:
        df["efficiency"] = efficiency_ratio(c, f.chop["period"])
        trending = df["efficiency"] >= f.chop["min_efficiency"]
        ok_long &= trending
        ok_short &= trending
    return ok_long, ok_short


def execution_hhmm(df: pd.DataFrame, bar_minutes: int) -> pd.Series:
    """New York HH:MM at which a signal on each candle would execute (the candle's end)."""
    local_end = ny_index(df.index + pd.Timedelta(minutes=bar_minutes))
    return pd.Series(local_end.strftime("%H:%M"), index=df.index)


def session_info(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """(New York date of each candle, candle number within its session starting at 0)."""
    day = pd.Series(ny_index(df.index).date, index=df.index)
    return day, day.groupby(day).cumcount()


def session_vwap(df: pd.DataFrame, day: pd.Series) -> pd.Series:
    typical = (df["high"] + df["low"] + df["close"]) / 3
    vol = df["volume"] if "volume" in df else pd.Series(1.0, index=df.index)
    return (typical * vol).groupby(day).cumsum() / vol.groupby(day).cumsum().replace(0, np.nan)


def prior_day_levels(df: pd.DataFrame, day: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Previous session's high and low, known from the open (NaN on the first session)."""
    daily = df.groupby(day).agg(high=("high", "max"), low=("low", "min"))
    prev = daily.shift(1)
    return day.map(prev["high"]), day.map(prev["low"])


def per_day_cap(flags: pd.Series, day: pd.Series, cap: int) -> pd.Series:
    """Keep only the first `cap` True values of each session."""
    count = flags.astype(int).groupby(day).cumsum()
    return flags & (count <= cap)


def _finish(df: pd.DataFrame, buy, sell, stop_dist=np.nan, target_dist=np.nan, cross=None) -> pd.DataFrame:
    buy, sell = np.asarray(buy, dtype=bool), np.asarray(sell, dtype=bool)
    df["signal"] = np.where(buy, BUY, np.where(sell, SELL, HOLD))
    df["cross"] = np.asarray(cross) if cross is not None else np.where(buy, 1, np.where(sell, -1, 0))
    df["stop_dist"] = np.where(buy | sell, stop_dist, np.nan)
    df["target_dist"] = np.where(buy | sell, target_dist, np.nan)
    return df


class _Base:
    name = "strategy"
    lines: dict = {}

    def __init__(self, cfg, stop=None, bar_minutes: int = 5, data_feed: str = "sip"):
        self.cfg, self.stop = cfg, stop
        self.bar_minutes, self.data_feed = bar_minutes, data_feed
        f = cfg.filters
        self.warmup = max(2 * 390 // bar_minutes, f.atr_band["period"] + 1,   # two sessions for prior-day levels
                          f.atr_band["lookback_bars"] if f.atr_band["enabled"] else 0,
                          f.htf_trend["ema"] * f.htf_trend["minutes"] // bar_minutes if f.htf_trend["enabled"] else 0)

    def _prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["atr"] = atr(df, self.cfg.filters.atr_band["period"])
        return df

    def _window(self, df, start=None, end=None) -> pd.Series:
        hhmm = execution_hhmm(df, self.bar_minutes)
        start = max(start or self.cfg.entry_start, self.cfg.entry_start)
        end = min(end or self.cfg.entry_end, self.cfg.entry_end)
        return (hhmm >= start) & (hhmm < end)


# ---------------------------------------------------------------------------
# EMA crossover (the original)
# ---------------------------------------------------------------------------
class EmaCross(_Base):
    """Fast/slow EMA crossover on completed candles, plus the optional filters."""

    name = "EMA crossover"

    def __init__(self, cfg, stop=None, bar_minutes: int = 5, data_feed: str = "sip"):
        super().__init__(cfg, stop, bar_minutes, data_feed)
        f = cfg.filters
        self.warmup = max(cfg.slow * 4, f.atr_band["period"] + 1,
                          f.atr_band["lookback_bars"] if f.atr_band["enabled"] else 0,
                          f.htf_trend["ema"] * f.htf_trend["minutes"] // bar_minutes if f.htf_trend["enabled"] else 0)
        self.lines = {"ema_fast": f"EMA{cfg.fast}", "ema_slow": f"EMA{cfg.slow}"}

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        cfg = self.cfg
        df = self._prepare(df)
        c = df["close"]
        df["ema_fast"], df["ema_slow"] = ema(c, cfg.fast), ema(c, cfg.slow)
        above = df["ema_fast"] > df["ema_slow"]
        prev = above.shift(1)
        ready = pd.Series(np.arange(len(df)) >= cfg.slow, index=df.index)
        cross = np.where(ready & above & (prev == False), 1, np.where(ready & ~above & (prev == True), -1, 0))  # noqa: E712
        ok_long, ok_short = filter_masks(df, cfg, self.bar_minutes, self.data_feed)
        win = self._window(df)
        buy = (cross == 1) & ok_long & win
        sell = (cross == -1) & ok_short & win
        return _finish(df, buy, sell, cross=cross)


# ---------------------------------------------------------------------------
# Opening range breakout
# ---------------------------------------------------------------------------
class OpeningRangeBreakout(_Base):
    """Trade the first close outside the opening range (the first `minutes` of the session).

    Long: a candle closes above the range high (optionally above VWAP too). Short: mirror.
    Stop: the other side of the range, or its midpoint. At most `max_trades_per_side` per
    side, per symbol, per day, and only until `last_entry`. Ranges narrower than
    `min_range_pct` or wider than `max_range_pct` of price are skipped (no clean level / too
    much risk)."""

    name = "Opening range breakout"

    def __init__(self, cfg, stop=None, bar_minutes: int = 5, data_feed: str = "sip"):
        super().__init__(cfg, stop, bar_minutes, data_feed)
        self.lines = {"or_high": "OR high", "or_low": "OR low", "vwap": "VWAP"}
        if cfg.orb["trend"] != "off" or cfg.orb["min_rvol"] > 0:   # 20-session history for those
            self.warmup = max(self.warmup, 22 * 390 // bar_minutes)

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.cfg.orb
        df = self._prepare(df)
        day, _ = session_info(df)
        # The range is set by the clock (candles starting 09:30 up to 09:30 + minutes), and a day
        # with any of those candles missing gets no range: counting "the first k candles" would
        # silently shift the range later whenever the opening candle is missing from the data.
        local = ny_index(df.index)
        since_open = pd.Series(local.hour * 60 + local.minute - 570, index=df.index)
        k = max(int(p["minutes"]) // self.bar_minutes, 1)
        in_range = (since_open >= 0) & (since_open < p["minutes"])
        complete = in_range.groupby(day).transform("sum") == k
        known = (since_open >= p["minutes"]) & complete
        df["or_high"] = df["high"].where(in_range).groupby(day).transform("max").where(known)
        df["or_low"] = df["low"].where(in_range).groupby(day).transform("min").where(known)
        df["vwap"] = session_vwap(df, day)
        c, prev_c = df["close"], df["close"].groupby(day).shift(1)
        width = (df["or_high"] - df["or_low"]) / c * 100
        sized = width.between(p["min_range_pct"], p["max_range_pct"])
        buffer = p["buffer_atr"] * df["atr"]
        up_level, down_level = df["or_high"] + buffer, df["or_low"] - buffer
        up = (c > up_level) & (prev_c <= up_level) & sized
        down = (c < down_level) & (prev_c >= down_level) & sized
        if p["vwap_confirm"]:
            up &= c > df["vwap"]
            down &= c < df["vwap"]
        # an opposite breakout (raw, uncapped, before the entry-only filters) closes an open position
        cross = np.where(up, 1, np.where(down, -1, 0))
        if p["min_rvol"] > 0:
            df["rvol"] = relative_volume(df)
            busy = df["rvol"] >= p["min_rvol"]
            up &= busy
            down &= busy
        if p["trend"] == "own":
            df["daily_trend"] = daily_trend(df, day)
            up &= df["daily_trend"] > 0
            down &= df["daily_trend"] < 0
        if p["entry"] == "retest":
            up, down = _retests(df, day, up, down, int(p["retest_bars"]))
        ok_long, ok_short = filter_masks(df, self.cfg, self.bar_minutes, self.data_feed)
        win = self._window(df, end=p["last_entry"])
        cap = int(p["max_trades_per_side"])
        buy = per_day_cap(up & ok_long & win, day, cap)
        sell = per_day_cap(down & ok_short & win, day, cap)
        if p["stop"] == "atr":
            stop = np.full(len(df), np.nan)              # risk.stop's ATR rule
        else:
            anchor_long = df["or_low"] if p["stop"] == "range" else (df["or_high"] + df["or_low"]) / 2
            anchor_short = df["or_high"] if p["stop"] == "range" else (df["or_high"] + df["or_low"]) / 2
            stop = np.where(buy, c - anchor_long, np.where(sell, anchor_short - c, np.nan))
        return _finish(df, buy, sell, stop_dist=stop, cross=cross)


def relative_volume(df: pd.DataFrame, sessions: int = 20) -> pd.Series:
    """Each candle's volume / the average volume of the same time slot over the prior sessions.
    (Live, this is measured on the paper feed's volume, e.g. IEX, so compare like with like.)"""
    slot = pd.Series(ny_index(df.index).strftime("%H:%M"), index=df.index)
    prior = df["volume"].groupby(slot).transform(lambda v: v.shift(1).rolling(sessions, min_periods=10).mean())
    return df["volume"] / prior


def daily_trend(df: pd.DataFrame, day: pd.Series, length: int = 20) -> pd.Series:
    """+1 / -1: was yesterday's close above / below its `length`-day average close (0 = unknown)."""
    closes = df["close"].groupby(day).last()
    sign = np.sign(closes - closes.rolling(length, min_periods=length * 3 // 4).mean()).shift(1)
    return day.map(sign).fillna(0)


def _retests(df: pd.DataFrame, day: pd.Series, up: pd.Series, down: pd.Series, bars: int):
    """Turn breakouts into retest entries: after a breakout close, wait up to `bars` candles for
    one that trades back to the range level and closes beyond it again. A close back inside the
    range cancels the setup; breakouts that never retest are simply missed."""
    hi_lvl, lo_lvl = df["or_high"].to_numpy(), df["or_low"].to_numpy()
    h, lo, c = (df[k].to_numpy() for k in ("high", "low", "close"))
    u, d, days = up.to_numpy(), down.to_numpy(), day.to_numpy()
    out_up, out_down = np.zeros(len(df), bool), np.zeros(len(df), bool)
    long_until = short_until = -1
    for i in range(len(df)):
        if i and days[i] != days[i - 1]:
            long_until = short_until = -1
        if i <= long_until:
            if c[i] <= hi_lvl[i]:
                long_until = -1                          # failed: back inside the range
            elif lo[i] <= hi_lvl[i]:
                out_up[i], long_until = True, -1         # touched the level and held it
        if i <= short_until:
            if c[i] >= lo_lvl[i]:
                short_until = -1
            elif h[i] >= lo_lvl[i]:
                out_down[i], short_until = True, -1
        if u[i]:
            long_until = i + bars
        if d[i]:
            short_until = i + bars
    return pd.Series(out_up, index=df.index), pd.Series(out_down, index=df.index)


# ---------------------------------------------------------------------------
# VWAP mean reversion
# ---------------------------------------------------------------------------
class VwapReversion(_Base):
    """Fade a stretched move back to the session VWAP.

    Long: within the last two candles price traded at least `dev_atr` ATRs below VWAP, and
    this candle closes above the previous candle's high (a reversal) while still below VWAP.
    Target: VWAP. Stop: the recent low minus `stop_buffer_atr` ATRs. Skipped when the
    target is closer than `min_rr` x the stop, and (optionally) when the move is a clean trend
    (efficiency ratio above `max_efficiency`), since trends don't snap back."""

    name = "VWAP mean reversion"

    def __init__(self, cfg, stop=None, bar_minutes: int = 5, data_feed: str = "sip"):
        super().__init__(cfg, stop, bar_minutes, data_feed)
        self.lines = {"vwap": "VWAP"}

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.cfg.vwap
        df = self._prepare(df)
        day, n = session_info(df)
        df["vwap"] = session_vwap(df, day)
        c, h, lo, a, vwap = df["close"], df["high"], df["low"], df["atr"], df["vwap"]
        g = lambda s: s.groupby(day)  # noqa: E731  (shift within the session only)
        low2 = pd.concat([lo, g(lo).shift(1)], axis=1).min(axis=1)
        high2 = pd.concat([h, g(h).shift(1)], axis=1).max(axis=1)
        stretched_down = (low2 - vwap) / a <= -p["dev_atr"]
        stretched_up = (high2 - vwap) / a >= p["dev_atr"]
        long_rev = stretched_down & (c > g(h).shift(1)) & (c < vwap)
        short_rev = stretched_up & (c < g(lo).shift(1)) & (c > vwap)
        long_stop = c - (low2 - p["stop_buffer_atr"] * a)
        short_stop = (high2 + p["stop_buffer_atr"] * a) - c
        long_tgt, short_tgt = vwap - c, c - vwap
        long_rev &= long_tgt >= p["min_rr"] * long_stop
        short_rev &= short_tgt >= p["min_rr"] * short_stop
        if p["max_efficiency"] < 1:
            calm = efficiency_ratio(c, 12) <= p["max_efficiency"]
            long_rev &= calm
            short_rev &= calm
        ok_long, ok_short = filter_masks(df, self.cfg, self.bar_minutes, self.data_feed)
        win = self._window(df, start=p["first_entry"], end=p["last_entry"])
        cap = int(p["max_trades_per_side"])
        buy = per_day_cap(long_rev & ok_long & win, day, cap)
        sell = per_day_cap(short_rev & ok_short & win, day, cap)
        stop = np.where(buy, long_stop, np.where(sell, short_stop, np.nan))
        target = np.where(buy, long_tgt, np.where(sell, short_tgt, np.nan))
        return _finish(df, buy, sell, stop_dist=stop, target_dist=target, cross=np.zeros(len(df), dtype=int))


# ---------------------------------------------------------------------------
# ICT: liquidity sweep -> market structure shift -> fair value gap
# ---------------------------------------------------------------------------
class IctSweepFvg(_Base):
    """ICT-style setup, made mechanical (bullish described; bearish is the mirror):

    1. Liquidity sweep: a candle trades below sell-side liquidity (the previous day's low or the
       latest confirmed swing low of the session) and closes back above it.
    2. Market structure shift: within `mss_window` candles, a close above the latest swing high
       that formed before the sweep (displacement).
    3. Fair value gap: in the move from the sweep to the shift, a three-candle gap where candle
       k-2's high is below candle k's low (at least `min_fvg_atr` ATRs tall).
    4. Entry: within `entry_window` candles after the shift, the first candle that dips into the
       gap and closes back above its bottom. Stop: below the sweep's low (plus a buffer).
       A close below the gap cancels the setup.

    Swing points are fractals with `pivot` candles on each side, so a swing is only known
    `pivot` candles after it happens (no look-ahead)."""

    name = "ICT sweep + MSS + FVG"

    def __init__(self, cfg, stop=None, bar_minutes: int = 5, data_feed: str = "sip"):
        super().__init__(cfg, stop, bar_minutes, data_feed)
        self.lines = {"pdh": "Prev day high", "pdl": "Prev day low"}

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.cfg.ict
        df = self._prepare(df)
        day, n = session_info(df)
        df["pdh"], df["pdl"] = prior_day_levels(df, day)
        o, h, lo, c = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
        a = df["atr"].to_numpy(dtype=float)
        pdh, pdl = df["pdh"].to_numpy(dtype=float), df["pdl"].to_numpy(dtype=float)
        days = day.to_numpy()
        piv = int(p["pivot"])
        sig = np.zeros(len(df), dtype=int)
        stop = np.full(len(df), np.nan)
        start = 0
        long_s = short_s = None
        swing_hi = swing_lo = None        # (price, index) of the latest confirmed swing in the session
        for i in range(len(df)):
            if i == 0 or days[i] != days[i - 1]:
                start, long_s, short_s, swing_hi, swing_lo = i, None, None, None, None
            j = i - piv                     # candle whose swing status becomes known now
            if j - piv >= start:
                win_l, win_h = lo[j - piv:i + 1], h[j - piv:i + 1]
                if lo[j] == win_l.min() and (win_l == lo[j]).sum() == 1:
                    swing_lo = (lo[j], j)
                if h[j] == win_h.max() and (win_h == h[j]).sum() == 1:
                    swing_hi = (h[j], j)
            if np.isnan(a[i]):
                continue
            long_s, sl = self._advance(1, i, long_s, o, h, lo, c, a, pdl[i], swing_lo, swing_hi, start, p)
            short_s, ss = self._advance(-1, i, short_s, o, h, lo, c, a, pdh[i], swing_hi, swing_lo, start, p)
            if sl is not None:
                sig[i], stop[i] = 1, sl
            elif ss is not None:
                sig[i], stop[i] = -1, ss
        ok_long, ok_short = filter_masks(df, self.cfg, self.bar_minutes, self.data_feed)
        win = self._window(df)
        cap = int(p["max_trades_per_side"])
        buy = per_day_cap(pd.Series(sig == 1, index=df.index) & ok_long & win, day, cap)
        sell = per_day_cap(pd.Series(sig == -1, index=df.index) & ok_short & win, day, cap)
        stop_dist = np.where(buy | sell, stop, np.nan)
        return _finish(df, buy, sell, stop_dist=stop_dist, cross=np.zeros(len(df), dtype=int))

    @staticmethod
    def _advance(d, i, state, o, h, lo, c, a, day_level, liq_swing, opp_swing, start, p):
        """One step of the long (d=+1) or short (d=-1) state machine. Prices are mirrored for
        shorts by flipping signs, so the same logic serves both. Returns (state, stop_dist)."""
        if d > 0:
            hi_, lo_, cl = h, lo, c
        else:   # mirror: a short is a long on the negated price series
            hi_, lo_, cl = -lo, -h, -c
        level_day = day_level if d > 0 else -day_level
        swing_liq = None if liq_swing is None else (liq_swing[0] if d > 0 else -liq_swing[0], liq_swing[1])
        swing_opp = None if opp_swing is None else (opp_swing[0] if d > 0 else -opp_swing[0], opp_swing[1])
        if state is None:
            levels = [x for x in (level_day, swing_liq[0] if swing_liq else None) if x is not None and not np.isnan(x)]
            swept = [x for x in levels if lo_[i] < x < cl[i]]
            if not swept:
                return None, None
            if swing_opp is not None and swing_opp[1] < i:
                mss = swing_opp[0]
            else:
                mss = hi_[max(start, i - 6):i].max() if i > start else np.nan
            if np.isnan(mss) or mss <= cl[i]:
                return None, None
            return {"stage": "mss", "sweep_low": lo_[i], "sweep_i": i, "mss": mss}, None
        if state["stage"] == "mss":
            state["sweep_low"] = min(state["sweep_low"], lo_[i])
            if i - state["sweep_i"] > p["mss_window"]:
                return None, None
            if cl[i] > state["mss"]:
                gap = None
                for k in range(i, state["sweep_i"] + 1, -1):
                    if hi_[k - 2] < lo_[k] and lo_[k] - hi_[k - 2] >= p["min_fvg_atr"] * a[i]:
                        gap = (hi_[k - 2], lo_[k])
                        break
                if gap is None:
                    return None, None
                return {**state, "stage": "entry", "gap": gap, "mss_i": i}, None
            return state, None
        # stage "entry": wait for a retest of the gap
        bottom, top = state["gap"]
        if i - state["mss_i"] > p["entry_window"] or cl[i] < bottom:
            return None, None
        if lo_[i] <= top and cl[i] >= bottom:
            stop_level = state["sweep_low"] - p["stop_buffer_atr"] * a[i]
            return None, cl[i] - stop_level
        return state, None


# ---------------------------------------------------------------------------
# Noise-area intraday momentum
# ---------------------------------------------------------------------------
class NoiseBreakout(_Base):
    """Trade only moves that leave the day's normal "noise area" (after Zarattini, Aziz & Barbon,
    "Beat the Market", 2024).

    Band for each time of day: the average absolute move from the session open at that time over
    the last `lookback` sessions (prior sessions only), times `band_mult`. Upper bound =
    max(open, yesterday's close) x (1 + band); lower = min(open, yesterday's close) x (1 - band).
    Checked only every `check_minutes` (from `first_check`): long when the close is above the
    upper bound (and VWAP), short below the lower bound (and VWAP). At each check a long is
    closed if price is back under max(upper bound, VWAP), a short above min(lower bound, VWAP);
    otherwise everything is closed at the flatten time. Protective stop for sizing: the base of
    the band (the open / yesterday's close), i.e. back across the whole noise area."""

    name = "Noise-area momentum"

    def __init__(self, cfg, stop=None, bar_minutes: int = 5, data_feed: str = "sip"):
        super().__init__(cfg, stop, bar_minutes, data_feed)
        self.warmup = max(self.warmup, (int(cfg.noise["lookback"]) + 2) * 390 // bar_minutes)
        self.lines = {"upper": "Noise top", "lower": "Noise bottom", "vwap": "VWAP"}

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.cfg.noise
        df = self._prepare(df)
        day, _ = session_info(df)
        slot = pd.Series(ny_index(df.index).strftime("%H:%M"), index=df.index)
        day_open = df["open"].groupby(day).transform("first")
        move = (df["close"] / day_open - 1).abs()
        n = int(p["lookback"])
        sigma = move.groupby(slot).transform(lambda m: m.shift(1).rolling(n, min_periods=n).mean())
        prev_close = day.map(df["close"].groupby(day).last().shift(1))
        top, bottom = np.maximum(day_open, prev_close), np.minimum(day_open, prev_close)
        band = p["band_mult"] * sigma
        df["upper"], df["lower"] = top * (1 + band), bottom * (1 - band)
        df["vwap"] = session_vwap(df, day)
        end = execution_hhmm(df, self.bar_minutes)
        minutes = end.str[:2].astype(int) * 60 + end.str[3:].astype(int)
        fh, fm = map(int, p["first_check"].split(":"))
        check = (minutes >= fh * 60 + fm) & ((minutes - 570) % int(p["check_minutes"]) == 0)
        c = df["close"]
        long_line = np.maximum(df["upper"], df["vwap"]) if p["use_vwap"] else df["upper"]
        short_line = np.minimum(df["lower"], df["vwap"]) if p["use_vwap"] else df["lower"]
        up, down = check & (c > long_line), check & (c < short_line)
        exit_long, exit_short = check & (c <= long_line), check & (c >= short_line)
        cross = np.select([up, down, exit_long & exit_short, exit_long, exit_short], [1, -1, EXIT_ANY, -1, 1], 0)
        ok_long, ok_short = filter_masks(df, self.cfg, self.bar_minutes, self.data_feed)
        win = self._window(df)
        buy, sell = up & ok_long & win, down & ok_short & win
        stop = np.where(buy, c - top, np.where(sell, bottom - c, np.nan))
        return _finish(df, buy, sell, stop_dist=stop, cross=cross)


STRATEGY_CLASSES = {"ema_cross": EmaCross, "orb": OpeningRangeBreakout, "vwap_reversion": VwapReversion,
                    "ict_fvg": IctSweepFvg, "noise_breakout": NoiseBreakout}


def build_strategy(cfg, stop=None, bar_minutes: int = 5, data_feed: str = "sip"):
    """The strategy object for strategy.type."""
    return STRATEGY_CLASSES[cfg.type](cfg, stop, bar_minutes, data_feed)
