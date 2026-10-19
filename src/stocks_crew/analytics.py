# analytics.py — pure pandas indicator & event math.
#
# Nothing in here knows about MCP, HTTP, or LLMs. It takes Series/DataFrames and
# returns Series/DataFrames, which makes it the easiest layer to unit test.
from __future__ import annotations

import numpy as np
import pandas as pd


def calc_sma(s: pd.Series, w: int = 20) -> pd.Series:
    """Calculate Simple Moving Average (SMA) for a given series.

    Args:
        s: Input price series (typically closing prices)
        w: Window size for the moving average (default: 20)

    Returns:
        Series containing the simple moving average values

    Note:
        Uses min_periods=max(3, w//2) to ensure reasonable data requirements
    """
    return s.rolling(w, min_periods=max(3, w // 2)).mean()


def calc_ema(s: pd.Series, w: int = 20) -> pd.Series:
    """Calculate Exponential Moving Average (EMA) for a given series.

    Args:
        s: Input price series (typically closing prices)
        w: Span parameter for the exponential moving average (default: 20)

    Returns:
        Series containing the exponential moving average values

    Note:
        EMA gives more weight to recent prices compared to SMA
    """
    return s.ewm(span=w, adjust=False).mean()


def calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Calculate Relative Strength Index (RSI) for a given price series.

    Args:
        close: Series of closing prices
        period: Number of periods for RSI calculation (default: 14)

    Returns:
        Series containing RSI values (0-100)

    Note:
        RSI > 70 typically indicates overbought conditions
        RSI < 30 typically indicates oversold conditions
    """
    delta = close.diff()
    up = delta.clip(lower=0.0)
    down = -delta.clip(upper=0.0)
    ma_up = up.ewm(alpha=1 / period, adjust=False).mean()
    ma_down = down.ewm(alpha=1 / period, adjust=False).mean()
    rs = ma_up / ma_down.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def flag_gaps(df: pd.DataFrame, threshold: float = 0.03) -> pd.DataFrame:
    """Identify gap up and gap down days in price data.

    Args:
        df: DataFrame with 'open' and 'close' columns
        threshold: Minimum gap size to flag (default: 0.03 = 3%)

    Returns:
        DataFrame with additional boolean columns:
            - gap_up: True when opening price is significantly above previous close
            - gap_down: True when opening price is significantly below previous close

    Note:
        Gap = (Open - Previous Close) / Previous Close
    """
    prev_close = df["close"].shift(1)
    gap = (df["open"] - prev_close) / prev_close
    df = df.copy()
    df["gap_up"] = gap >= threshold
    df["gap_down"] = gap <= -threshold
    return df


def flag_volatility(df: pd.DataFrame, window: int = 20, mult: float = 2.0) -> pd.DataFrame:
    """Identify days with unusually high volatility (volatility spikes).

    Args:
        df: DataFrame with 'close' column
        window: Rolling window for volatility calculation (default: 20)
        mult: Multiplier for volatility threshold (default: 2.0)

    Returns:
        DataFrame with additional boolean column:
            - vol_spike: True when daily return exceeds mult * rolling volatility

    Note:
        Volatility spikes can indicate significant market events or news
    """
    ret = df["close"].pct_change()
    vol = ret.rolling(window, min_periods=5).std()
    df = df.copy()
    df["vol_spike"] = ret.abs() > (mult * vol)
    return df


def flag_52w_extremes(df: pd.DataFrame) -> pd.DataFrame:
    """Identify 52-week highs and lows in price data.

    Args:
        df: DataFrame with 'close' column

    Returns:
        DataFrame with additional boolean columns:
            - is_52w_high: True when close equals 52-week rolling maximum
            - is_52w_low: True when close equals 52-week rolling minimum

    Note:
        Uses 252 trading days (approximately 1 year) with minimum 30 days of data
    """
    df = df.copy()
    roll_max = df["close"].rolling(252, min_periods=30).max()
    roll_min = df["close"].rolling(252, min_periods=30).min()
    df["is_52w_high"] = df["close"] >= roll_max
    df["is_52w_low"] = df["close"] <= roll_min
    return df


def coerce_close(df: pd.DataFrame) -> pd.Series:
    """Extract and validate close price series from DataFrame.

    Args:
        df: DataFrame potentially containing 'close' column

    Returns:
        Numeric Series of close prices, or empty Series if invalid input

    Note:
        - Converts close prices to numeric, coercing errors to NaN
        - Drops NaN values from the result
        - Returns empty Series if DataFrame is None, empty, or missing 'close' column
    """
    if df is None or df.empty or "close" not in df.columns:
        return pd.Series(dtype="float64")
    return pd.to_numeric(df["close"], errors="coerce").dropna()
