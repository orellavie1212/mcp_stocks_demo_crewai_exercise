# mcp_server.py
from __future__ import annotations

import json
import os
import numpy as np
import pandas as pd
from typing import Dict, Any

from mcp.server.fastmcp import FastMCP

# yfinance-backed datasource functions (your updated datasource.py)
from datasource import (
    search_symbols as ds_search,
    latest_quote as ds_quote,
    price_series as ds_series,
)

mcp = FastMCP("stocks-analyzer")

# ---------- indicators & helpers ----------
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

def calc_cagr(price_curve: pd.Series, periods_per_year: int = 252):
    """Calculate Compound Annual Growth Rate (CAGR) for a price series.
    
    Args:
        price_curve: Series of prices (e.g., equity curve, stock prices)
        periods_per_year: Number of trading periods per year (default: 252 for daily data)
        
    Returns:
        CAGR as a decimal (e.g., 0.15 for 15% annual return)
        Returns NaN if insufficient data or invalid calculation
        
    Note:
        CAGR = (End Value / Start Value)^(1/years) - 1
    """
    if len(price_curve) < 2:
        return float("nan")
    ret = float(price_curve.iloc[-1]) / float(price_curve.iloc[0])
    yrs = len(price_curve) / periods_per_year
    return float(ret ** (1 / yrs) - 1) if yrs > 0 else float("nan")


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

def _coerce_close(df: pd.DataFrame) -> pd.Series:
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

# ---------- MCP tools ----------
@mcp.tool()
def search_symbols(query: str) -> str:
    """Symbol lookup by company name/ticker. Returns a JSON array."""
    try:
        return json.dumps(ds_search(query), ensure_ascii=False)
    except Exception as e:
        return json.dumps([{"error": "search_failed", "message": str(e)}])

@mcp.tool()
def latest_quote(symbol: str) -> str:
    """Latest price, change %, volume. Returns a JSON object."""
    try:
        return json.dumps(ds_quote(symbol), ensure_ascii=False)
    except Exception as e:
        return json.dumps({"symbol": symbol, "error": "quote_failed", "message": str(e)})
# mcp_server.py -> price_series tool
@mcp.tool()
def price_series(symbol: str, interval: str = "daily", lookback: int = 180) -> str:
    """OHLCV series as a JSON array (date ISO)."""
    try:
        df = ds_series(symbol, interval, lookback)
        # Guarantee expected columns even if empty
        for col in ["date", "open", "high", "low", "close", "volume"]:
            if col not in df.columns:
                df[col] = pd.Series(dtype="float64" if col != "date" else "datetime64[ns]")
        return df.to_json(orient="records", date_format="iso")
    except Exception as e:
        return json.dumps({"symbol": symbol, "error": "series_failed", "message": str(e)})

@mcp.tool()
def indicators(
    symbol: str,
    window_sma: int = 20,
    window_ema: int = 50,
    window_rsi: int = 14,
) -> str:
    """SMA/EMA/RSI and last snapshot. Returns a JSON object."""
    try:
        df = ds_series(symbol, "daily", 300)
        close = _coerce_close(df)
        if close.empty:
            return json.dumps({"symbol": symbol, "error": "no_data", "message": f"No data available for symbol {symbol}"})
        sma = calc_sma(close, window_sma).iloc[-1]
        ema = calc_ema(close, window_ema).iloc[-1]
        rsi = calc_rsi(close, window_rsi).iloc[-1]
        out = {
            "symbol": symbol,
            "last_close": float(close.iloc[-1]),
            "sma": float(sma) if pd.notna(sma) else None,
            "ema": float(ema) if pd.notna(ema) else None,
            "rsi": float(rsi) if pd.notna(rsi) else None,
        }
        return json.dumps(out)
    except Exception as e:
        return json.dumps({"symbol": symbol, "error": "indicators_failed", "message": str(e)})

@mcp.tool()
def detect_events(symbol: str) -> str:
    """Gap up/down, volatility spikes, 52w extremes on the last bar. Returns a JSON object."""
    try:
        df = ds_series(symbol, "daily", 400)
        if df is None or df.empty:
            return json.dumps({"symbol": symbol, "error": "no_data", "message": f"No data available for symbol {symbol}"})
        # ensure numeric
        for c in ["open", "high", "low", "close", "volume"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=["open", "close"]).reset_index(drop=True)
        if df.empty:
            return json.dumps({"symbol": symbol, "error": "no_data", "message": f"No valid data available for symbol {symbol}"})

        df = flag_gaps(df)
        df = flag_volatility(df)
        df = flag_52w_extremes(df)
        last_row = df.iloc[-1]
        last = {
            "symbol": symbol,
            "date": str(pd.to_datetime(last_row["date"]).date()) if "date" in df.columns else None,
            "gap_up": bool(last_row.get("gap_up", False)),
            "gap_down": bool(last_row.get("gap_down", False)),
            "vol_spike": bool(last_row.get("vol_spike", False)),
            "is_52w_high": bool(last_row.get("is_52w_high", False)),
            "is_52w_low": bool(last_row.get("is_52w_low", False)),
        }
        return json.dumps(last)
    except Exception as e:
        return json.dumps({"symbol": symbol, "error": "events_failed", "message": str(e)})


@mcp.tool()
def explain(
    symbol: str,
    language: str = "en",
    tone: str = "neutral",
    risk_profile: str = "balanced",
    horizon_days: int = 30,
    bullets: bool = True,
    openai_api_key: str = "",
) -> str:
    """
    LLM explanation of the current technical snapshot with guardrails.
    Returns a JSON object: {"text": "...", "rationale": [...], "disclaimers": "..."}.
    """
    import json as _json

    def _safe_json(s):
        try:
            return _json.loads(s) if isinstance(s, str) else (s or {})
        except Exception:
            return {}

    # Check for required API key
    if not openai_api_key:
        return json.dumps({
            "error": "openai_api_key_required", 
            "message": "OpenAI API key is required for LLM explanations"
        })

    # Gather fresh local context (no external calls here)
    ind = _safe_json(indicators(symbol))
    evt = _safe_json(detect_events(symbol))

    # LLM path with strict guardrails and JSON schema
    try:
        from openai import OpenAI
        client = OpenAI(api_key=openai_api_key)

        # System message
        system_msg = (
            "You are an impartial market analyst. Summarize technical signals clearly, "
            "avoid predictions and avoid financial advice. Use short, concrete language. "
            "If inputs are missing, acknowledge uncertainty. Output in the requested language."
        )

        # User prompt with all context (indicators + events + knobs)
        prompt = {
            "symbol": symbol,
            "language": language,
            "tone": tone,
            "risk_profile": risk_profile,
            "horizon_days": horizon_days,
            "bullets": bool(bullets),
            "indicators": ind,
            "events": evt,
            "instructions": [
                "Keep it under ~120 words if bullets=False, or 3-5 bullets if bullets=True.",
                "No investment advice. No price targets.",
                "Explain what each signal implies in plain language.",
                "If RSI or MAs are missing, say so briefly.",
                "Mention 52-week context if flagged."
            ],
        }

        # JSON schema for structured output
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "tech_summary",
                "schema": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "rationale": {"type": "array", "items": {"type": "string"}},
                        "disclaimers": {"type": "string"}
                    },
                    "required": ["text", "disclaimers"]
                }
            },
        }

        # Compose messages + call
        msgs = [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": f"Generate a technical summary:\n{json.dumps(prompt, ensure_ascii=False)}"},
        ]

        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=msgs,
            temperature=0.2,
            response_format=response_format,
        )
        content = (r.choices[0].message.content or "").strip()
        # If model didn't honor JSON schema, wrap as text
        try:
            _ = json.loads(content)
            return content
        except Exception:
            return json.dumps({"text": content or "", "disclaimers": "Not investment advice."}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "error": "llm_explanation_failed", 
            "message": f"Failed to generate LLM explanation: {str(e)}"
        })