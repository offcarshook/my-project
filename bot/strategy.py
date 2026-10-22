"""Prop Pass strategy: EMA crossover on completed candles with optional filters."""
import numpy as np
import pandas as pd

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


def stop_distance(price, atr_value, stop) -> float | np.ndarray:
    """Stop distance in price units from a StopConfig: ATR-based (clamped to min/max % of
    price) or a fixed % of price."""
    if stop.type == "fixed":
        return price * stop.fixed_pct / 100
    return np.clip(stop.atr_mult * atr_value, price * stop.min_pct / 100, price * stop.max_pct / 100)


class EmaCross:
    """Prop Pass strategy: fast/slow EMA crossover on completed candles, plus optional
    lightweight filters (all off unless enabled in the config).

    Output columns:
      cross  +1 / -1 on the candle where fast crosses above / below slow (raw, unfiltered;
             used to exit on an opposite signal)
      signal BUY / SELL / HOLD: a cross that passed every enabled filter and whose next
             candle (where the order would execute) opens inside the entry window
      atr, stop_dist, plus each filter's inputs for inspection
    """

    name = "EMA crossover"

    def __init__(self, cfg, stop=None, bar_minutes: int = 5, data_feed: str = "sip"):
        self.cfg, self.stop = cfg, stop
        self.bar_minutes, self.data_feed = bar_minutes, data_feed
        f = cfg.filters
        self.warmup = max(cfg.slow * 4, f.atr_band["period"] + 1,
                          f.atr_band["lookback_bars"] if f.atr_band["enabled"] else 0,
                          f.htf_trend["ema"] * f.htf_trend["minutes"] // bar_minutes if f.htf_trend["enabled"] else 0)
        self.lines = {"ema_fast": f"EMA{cfg.fast}", "ema_slow": f"EMA{cfg.slow}"}

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        from bot.indicators import atr, efficiency_ratio, ema, htf_close_and_ema, ny_index

        cfg, f = self.cfg, self.cfg.filters
        df = df.copy()
        c = df["close"]
        df["ema_fast"], df["ema_slow"] = ema(c, cfg.fast), ema(c, cfg.slow)
        df["atr"] = atr(df, f.atr_band["period"])
        above = df["ema_fast"] > df["ema_slow"]
        prev = above.shift(1)
        ready = pd.Series(np.arange(len(df)) >= cfg.slow, index=df.index)
        cross = np.where(ready & above & (prev == False), 1, np.where(ready & ~above & (prev == True), -1, 0))  # noqa: E712
        df["cross"] = cross

        ok_long = pd.Series(True, index=df.index) & df["atr"].notna()
        ok_short = ok_long.copy()
        if f.htf_trend["enabled"]:
            htf = htf_close_and_ema(df, self.bar_minutes, f.htf_trend["minutes"], f.htf_trend["ema"])
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
            floor = f.liquidity["min_dollar_volume_iex" if self.data_feed == "iex" else "min_dollar_volume_sip"]
            df["dollar_volume"] = c * df["volume"]
            liquid = df["dollar_volume"] >= floor
            ok_long &= liquid
            ok_short &= liquid
        if f.chop["enabled"]:
            df["efficiency"] = efficiency_ratio(c, f.chop["period"])
            trending = df["efficiency"] >= f.chop["min_efficiency"]
            ok_long &= trending
            ok_short &= trending

        # Orders execute when the signal candle closes, i.e. at the next candle's open.
        local_end = ny_index(df.index + pd.Timedelta(minutes=self.bar_minutes))
        hhmm = pd.Series(local_end.strftime("%H:%M"), index=df.index)
        in_window = (hhmm >= cfg.entry_start) & (hhmm < cfg.entry_end)
        buy = (df["cross"] == 1) & ok_long & in_window
        sell = (df["cross"] == -1) & ok_short & in_window
        df["signal"] = np.where(buy, BUY, np.where(sell, SELL, HOLD))
        df["stop_dist"] = stop_distance(c, df["atr"], self.stop) if self.stop is not None else np.nan
        return df

