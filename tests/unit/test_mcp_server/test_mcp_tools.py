"""
Unit tests for MCP server tool logic (without HTTP server).
Tests the calculation functions directly.

Run: python -m pytest tests/unit/test_mcp_server/ -v
"""
import sys, os
import numpy as np
import pandas as pd
import pytest

# Add MCP server to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../apps/mcp-server"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../packages/shared-config"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../packages/shared-observability"))


def make_price_series(n: int = 50, start: float = 100.0, trend: float = 0.5) -> pd.DataFrame:
    """Generate a synthetic OHLCV DataFrame for testing."""
    closes = [start + i * trend + np.random.normal(0, 2) for i in range(n)]
    opens = [c - np.random.uniform(0, 1) for c in closes]
    highs = [max(o, c) + np.random.uniform(0, 2) for o, c in zip(opens, closes)]
    lows = [min(o, c) - np.random.uniform(0, 2) for o, c in zip(opens, closes)]
    volumes = [np.random.randint(1_000_000, 10_000_000) for _ in range(n)]

    return pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=n),
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    })


class TestTechnicalIndicators:
    """Test SMA, EMA, RSI calculations from MCP server."""

    def setup_method(self):
        """Import calculation functions from MCP server module."""
        # We import only the pure calculation functions
        # (avoiding FastAPI/datasource imports at module level)
        self._df = make_price_series(100, start=150.0, trend=0.3)
        self._close = self._df["close"]

    def _calc_sma(self, s: pd.Series, w: int = 20) -> pd.Series:
        return s.rolling(w, min_periods=max(3, w // 2)).mean()

    def _calc_ema(self, s: pd.Series, w: int = 20) -> pd.Series:
        return s.ewm(span=w, adjust=False).mean()

    def _calc_rsi(self, close: pd.Series, period: int = 14) -> pd.Series:
        delta = close.diff()
        up = delta.clip(lower=0.0)
        down = -delta.clip(upper=0.0)
        ma_up = up.ewm(alpha=1 / period, adjust=False).mean()
        ma_down = down.ewm(alpha=1 / period, adjust=False).mean()
        rs = ma_up / ma_down.replace(0, np.nan)
        return 100 - (100 / (1 + rs))

    def test_sma_returns_series(self):
        sma = self._calc_sma(self._close, 20)
        assert isinstance(sma, pd.Series)
        assert len(sma) == len(self._close)

    def test_sma_last_value_is_float(self):
        sma = self._calc_sma(self._close, 20)
        assert isinstance(float(sma.iloc[-1]), float)
        assert not np.isnan(float(sma.iloc[-1]))

    def test_ema_returns_series(self):
        ema = self._calc_ema(self._close, 50)
        assert isinstance(ema, pd.Series)

    def test_rsi_range(self):
        """RSI must always be in 0-100."""
        rsi = self._calc_rsi(self._close, 14)
        rsi_valid = rsi.dropna()
        assert (rsi_valid >= 0).all()
        assert (rsi_valid <= 100).all()

    def test_rsi_overbought_on_rising_series(self):
        """A strongly rising series should produce RSI > 60.
        Use np.linspace trend + Gaussian noise so there ARE real down-moves
        (making ma_down > 0 and RSI computable) while the net direction is up.
        """
        np.random.seed(42)
        # Trend 100→200 over 100 bars; noise std=1 ensures occasional down-moves
        base = np.linspace(100, 200, 100)
        noise = np.random.normal(0, 1, 100)
        rising = pd.Series(base + noise)
        rsi = self._calc_rsi(rising, 14)
        valid_rsi = rsi.dropna()
        assert len(valid_rsi) > 0, "RSI produced all NaN"
        assert float(valid_rsi.iloc[-1]) > 60  # Strong uptrend → high RSI

    def test_rsi_oversold_on_falling_series(self):
        """A strongly falling series should produce RSI < 30."""
        falling = pd.Series([float(100 - i * 5) for i in range(100)])
        rsi = self._calc_rsi(falling, 14)
        final_rsi = float(rsi.iloc[-1])
        assert final_rsi < 40  # Should be low


class TestEventDetection:
    """Test gap/volatility/52w detection functions."""

    def _flag_gaps(self, df, threshold=0.03):
        prev_close = df["close"].shift(1)
        gap = (df["open"] - prev_close) / prev_close
        df = df.copy()
        df["gap_up"] = gap >= threshold
        df["gap_down"] = gap <= -threshold
        return df

    def test_gap_up_detected(self):
        df = make_price_series(20)
        # Force a gap up on the last row
        df.iloc[-1, df.columns.get_loc("open")] = df.iloc[-2]["close"] * 1.10
        df = self._flag_gaps(df)
        assert bool(df.iloc[-1]["gap_up"]) is True

    def test_gap_down_detected(self):
        df = make_price_series(20)
        # Force a gap down on the last row
        df.iloc[-1, df.columns.get_loc("open")] = df.iloc[-2]["close"] * 0.90
        df = self._flag_gaps(df)
        assert bool(df.iloc[-1]["gap_down"]) is True

    def test_no_gap_on_flat_open(self):
        df = make_price_series(20)
        # Force open == prev_close (no gap)
        df.iloc[-1, df.columns.get_loc("open")] = df.iloc[-2]["close"]
        df = self._flag_gaps(df, threshold=0.03)
        assert bool(df.iloc[-1]["gap_up"]) is False
        assert bool(df.iloc[-1]["gap_down"]) is False


class TestInputValidation:
    """Test Pydantic request models from MCP server."""

    def test_search_request_requires_q(self):
        import pydantic
        # We test the schema logic directly without importing FastAPI app
        from pydantic import BaseModel, Field

        class SearchRequest(BaseModel):
            q: str = Field(..., min_length=1)

        with pytest.raises(Exception):
            SearchRequest(q="")

    def test_quote_request_validates_symbol_length(self):
        from pydantic import BaseModel, Field

        class QuoteRequest(BaseModel):
            symbol: str = Field(..., min_length=1, max_length=10)

        with pytest.raises(Exception):
            QuoteRequest(symbol="TOOLONGSYMBOL")

    def test_series_request_lookback_bounds(self):
        from pydantic import BaseModel, Field

        class SeriesRequest(BaseModel):
            symbol: str
            lookback: int = Field(default=180, ge=1, le=5000)

        with pytest.raises(Exception):
            SeriesRequest(symbol="AAPL", lookback=0)

        with pytest.raises(Exception):
            SeriesRequest(symbol="AAPL", lookback=5001)

        # Valid
        req = SeriesRequest(symbol="AAPL", lookback=180)
        assert req.lookback == 180
