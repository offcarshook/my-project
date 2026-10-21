"""SMA crossover strategy."""
import pandas as pd

BUY, SELL, HOLD = "BUY", "SELL", "HOLD"


def add_smas(df: pd.DataFrame, short: int, long: int) -> pd.DataFrame:
    df = df.copy()
    df["sma_short"] = df["close"].rolling(short).mean()
    df["sma_long"] = df["close"].rolling(long).mean()
    return df


def crossovers(df: pd.DataFrame) -> pd.Series:
    """BUY where short crosses above long, SELL where it crosses below, else HOLD."""
    above = df["sma_short"] > df["sma_long"]
    valid = df["sma_short"].notna() & df["sma_long"].notna()
    prev_above = above.shift(1)
    prev_valid = valid.shift(1, fill_value=False)
    signal = pd.Series(HOLD, index=df.index)
    ready = valid & prev_valid
    signal[ready & above & (prev_above == False)] = BUY  # noqa: E712
    signal[ready & ~above & (prev_above == True)] = SELL  # noqa: E712
    return signal


def latest_signal(df: pd.DataFrame, short: int, long: int) -> str:
    if len(df) < long + 1:
        return HOLD
    return crossovers(add_smas(df, short, long)).iloc[-1]
