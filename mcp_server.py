from __future__ import annotations

import json
import os
import time
import numpy as np
import pandas as pd
from typing import Dict, Any, List
from datetime import datetime

from mcp.server.fastmcp import FastMCP

try:
    from crewai.tools import tool
    CREWAI_AVAILABLE = True
except ImportError:
    CREWAI_AVAILABLE = False
    def tool(name: str = None):
        def decorator(func):
            func.name = name or func.__name__
            return func
        return decorator

TOOL_TRACE: List[Dict[str, Any]] = []

def _log_tool_call(tool_name: str, arguments: Dict[str, Any], start_time: float, 
                   success: bool, result: str = "", error: str = ""):
    """Log tool call for tracing and debugging"""
    duration = time.time() - start_time
    trace_entry = {
        "timestamp": datetime.now().isoformat(),
        "tool_name": tool_name,
        "arguments": arguments,
        "duration_seconds": round(duration, 3),
        "success": success,
        "result_preview": result[:200] if result else "",
        "error": error if not success else None
    }
    TOOL_TRACE.append(trace_entry)
    return trace_entry

def get_tool_trace() -> List[Dict[str, Any]]:
    """Get the complete tool call trace"""
    return TOOL_TRACE.copy()

def clear_tool_trace():
    """Clear the tool call trace"""
    TOOL_TRACE.clear()

from datasource import (
    search_symbols as ds_search,
    latest_quote as ds_quote,
    price_series as ds_series,
)

mcp = FastMCP("stocks-analyzer")

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

@mcp.tool()
def price_series(symbol: str, interval: str = "daily", lookback: int = 180) -> str:
    """OHLCV series as a JSON array (date ISO)."""
    try:
        df = ds_series(symbol, interval, lookback)
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
    gemini_api_key: str = "",
) -> str:
    """
    LLM explanation of the current technical snapshot with guardrails.
    Returns a JSON object: {"text": "...", "rationale": [...], "disclaimers": "..."}.
    Uses Gemini (gemini-2.5-flash) instead of OpenAI.
    """
    import json as _json

    def _safe_json(s):
        try:
            return _json.loads(s) if isinstance(s, str) else (s or {})
        except Exception:
            return {}

    if not gemini_api_key:
        return json.dumps({
            "error": "gemini_api_key_required",
            "message": "Gemini API key is required for LLM explanations"
        })

    ind = _safe_json(indicators(symbol))
    evt = _safe_json(detect_events(symbol))

    try:
        from langchain_google_genai import ChatGoogleGenerativeAI

        llm = ChatGoogleGenerativeAI(
            model="gemini-2.5-flash",
            google_api_key=gemini_api_key,
            temperature=0.2,
        )

        bullet_note = "Return 3-5 bullet points." if bullets else "Return 2-3 short paragraphs."
        prompt_text = (
            f"You are an impartial market analyst. Analyze the technical data below for {symbol}.\n"
            f"Language: {language} | Tone: {tone} | Risk profile: {risk_profile} | "
            f"Horizon: {horizon_days} days\n\n"
            f"Indicators: {json.dumps(ind, ensure_ascii=False)}\n"
            f"Events: {json.dumps(evt, ensure_ascii=False)}\n\n"
            f"Rules:\n"
            f"- {bullet_note}\n"
            f"- No investment advice, no price targets.\n"
            f"- If a value is missing, acknowledge it briefly.\n"
            f"- Mention 52-week context if flagged.\n\n"
            f"Return ONLY this valid JSON (no markdown fences):\n"
            f'{{ "text": "...", "rationale": ["..."], "disclaimers": "Not investment advice." }}'
        )

        response = llm.invoke(prompt_text)
        content = (response.content or "").strip()

        if content.startswith("```"):
            parts = content.split("```")
            content = parts[1] if len(parts) > 1 else content
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()

        try:
            _ = json.loads(content)
            return content
        except Exception:
            return json.dumps(
                {"text": content or "", "disclaimers": "Not investment advice."},
                ensure_ascii=False,
            )

    except Exception as e:
        return json.dumps({
            "error": "llm_explanation_failed",
            "message": f"Failed to generate LLM explanation: {str(e)}"
        })


def _parse_search_results(json_str: str) -> str:
    """Parse search results JSON into readable format"""
    try:
        data = json.loads(json_str)
        if isinstance(data, list):
            if len(data) == 0:
                return "No matching symbols found."
            result_lines = ["Found matching symbols:"]
            for item in data:
                if "error" in item:
                    return f"Error: {item.get('message', 'Unknown error')}"
                symbol = item.get("symbol", "N/A")
                name = item.get("name", "N/A")
                region = item.get("region", "")
                currency = item.get("currency", "")
                result_lines.append(f"  • {symbol}: {name} ({region} {currency})".strip())
            return "\n".join(result_lines)
        return json_str
    except:
        return json_str

def _parse_quote(json_str: str) -> str:
    """Parse quote JSON into readable format"""
    try:
        data = json.loads(json_str)
        if "error" in data:
            return f"Error: {data.get('message', 'Unknown error')}"
        symbol = data.get("symbol", "N/A")
        price = data.get("price")
        change = data.get("change")
        change_pct = data.get("change_percent")
        volume = data.get("volume")
        
        result = f"Quote for {symbol}:\n"
        if price is not None:
            result += f"  Current Price: ${price:.2f}\n"
        if change is not None and change_pct is not None:
            sign = "+" if change >= 0 else ""
            result += f"  Change: {sign}${change:.2f} ({sign}{change_pct:.2f}%)\n"
        if volume is not None:
            result += f"  Volume: {volume:,.0f}\n"
        return result.strip()
    except:
        return json_str

def _parse_price_series(json_str: str) -> str:
    """Parse price series JSON into readable summary"""
    try:
        data = json.loads(json_str)
        if isinstance(data, list):
            if len(data) == 0:
                return "No price data available."
            if "error" in data[0] if data else {}:
                return f"Error: {data[0].get('message', 'Unknown error')}"
            
            closes = [float(item.get("close", 0)) for item in data if item.get("close")]
            if closes:
                result = f"Price Series Summary ({len(data)} days):\n"
                result += f"  First Close: ${closes[0]:.2f}\n"
                result += f"  Last Close: ${closes[-1]:.2f}\n"
                result += f"  High: ${max(closes):.2f}\n"
                result += f"  Low: ${min(closes):.2f}\n"
                if len(closes) > 1:
                    change = closes[-1] - closes[0]
                    change_pct = (change / closes[0]) * 100
                    sign = "+" if change >= 0 else ""
                    result += f"  Period Change: {sign}${change:.2f} ({sign}{change_pct:.2f}%)\n"
                return result.strip()
        return json_str
    except:
        return json_str

def _parse_indicators(json_str: str) -> str:
    """Parse indicators JSON into readable format"""
    try:
        data = json.loads(json_str)
        if "error" in data:
            return f"Error: {data.get('message', 'Unknown error')}"
        
        symbol = data.get("symbol", "N/A")
        last_close = data.get("last_close")
        sma = data.get("sma")
        ema = data.get("ema")
        rsi = data.get("rsi")
        
        result = f"Technical Indicators for {symbol}:\n"
        if last_close is not None:
            result += f"  Last Close: ${last_close:.2f}\n"
        if sma is not None:
            result += f"  SMA(20): ${sma:.2f}\n"
        if ema is not None:
            result += f"  EMA(50): ${ema:.2f}\n"
        if rsi is not None:
            result += f"  RSI(14): {rsi:.2f}"
            if rsi > 70:
                result += " (Overbought)"
            elif rsi < 30:
                result += " (Oversold)"
            result += "\n"
        return result.strip()
    except:
        return json_str

def _parse_events(json_str: str) -> str:
    """Parse events JSON into readable format"""
    try:
        data = json.loads(json_str)
        if "error" in data:
            return f"Error: {data.get('message', 'Unknown error')}"
        
        symbol = data.get("symbol", "N/A")
        date = data.get("date", "N/A")
        events = []
        
        if data.get("gap_up"):
            events.append("Gap Up")
        if data.get("gap_down"):
            events.append("Gap Down")
        if data.get("vol_spike"):
            events.append("Volatility Spike")
        if data.get("is_52w_high"):
            events.append("52-Week High")
        if data.get("is_52w_low"):
            events.append("52-Week Low")
        
        result = f"Market Events for {symbol} (as of {date}):\n"
        if events:
            result += "  " + ", ".join(events)
        else:
            result += "  No significant events detected"
        return result
    except:
        return json_str


@tool("search_symbols")
def search_symbols_tool(q: str) -> str:
    """
    Search for stock symbols by company name or ticker.
    
    YOU MUST CALL THIS TOOL to search for stock symbols. Do not guess or make up symbols.
    
    Args:
        q: Company name or ticker symbol to search for
    
    Returns:
        Formatted list of matching stock symbols with company names
    """
    start_time = time.time()
    try:
        json_result = search_symbols(q)
        parsed_result = _parse_search_results(json_result)
        _log_tool_call("search_symbols", {"q": q}, start_time, True, parsed_result)
        return parsed_result
    except Exception as e:
        error_msg = str(e)
        _log_tool_call("search_symbols", {"q": q}, start_time, False, error=error_msg)
        return f"Error: {error_msg}"

@tool("get_quote")
def get_quote_tool(symbol: str) -> str:
    """
    Get latest price, change percentage, and volume for a stock.
    
    YOU MUST CALL THIS TOOL to get current stock prices. Do not estimate or guess prices.
    
    Args:
        symbol: Stock ticker symbol (e.g., 'AAPL', 'NVDA', 'TSLA')
    
    Returns:
        Formatted quote data with current price, change, and volume
    """
    start_time = time.time()
    try:
        json_result = latest_quote(symbol)
        parsed_result = _parse_quote(json_result)
        _log_tool_call("get_quote", {"symbol": symbol}, start_time, True, parsed_result)
        return parsed_result
    except Exception as e:
        error_msg = str(e)
        _log_tool_call("get_quote", {"symbol": symbol}, start_time, False, error=error_msg)
        return f"Error: {error_msg}"

@tool("get_price_series")
def get_price_series_tool(symbol: str) -> str:
    """
    Get historical OHLCV (Open, High, Low, Close, Volume) price data for a stock.
    
    YOU MUST CALL THIS TOOL to get historical price data. Do not make up historical prices.
    
    Args:
        symbol: Stock ticker symbol (e.g., 'AAPL', 'NVDA', 'TSLA')
    
    Returns:
        Formatted summary of historical price data with key statistics
    """
    start_time = time.time()
    try:
        json_result = price_series(symbol)
        parsed_result = _parse_price_series(json_result)
        _log_tool_call("get_price_series", {"symbol": symbol}, start_time, True, parsed_result)
        return parsed_result
    except Exception as e:
        error_msg = str(e)
        _log_tool_call("get_price_series", {"symbol": symbol}, start_time, False, error=error_msg)
        return f"Error: {error_msg}"

@tool("get_indicators")
def get_indicators_tool(symbol: str, window_sma: int = 20, window_ema: int = 50, window_rsi: int = 14) -> str:
    """
    Get technical indicators (SMA, EMA, RSI) for a stock.
    
    YOU MUST CALL THIS TOOL to calculate technical indicators. Do not calculate or estimate indicators yourself.
    
    Args:
        symbol: Stock ticker symbol (e.g., 'AAPL', 'NVDA', 'TSLA')
        window_sma: Window size for Simple Moving Average (default: 20)
        window_ema: Window size for Exponential Moving Average (default: 50)
        window_rsi: Period for Relative Strength Index (default: 14)
    
    Returns:
        Formatted technical indicator values with interpretations
    """
    start_time = time.time()
    try:
        json_result = indicators(symbol, window_sma, window_ema, window_rsi)
        parsed_result = _parse_indicators(json_result)
        _log_tool_call("get_indicators", {
            "symbol": symbol,
            "window_sma": window_sma,
            "window_ema": window_ema,
            "window_rsi": window_rsi
        }, start_time, True, parsed_result)
        return parsed_result
    except Exception as e:
        error_msg = str(e)
        _log_tool_call("get_indicators", {
            "symbol": symbol,
            "window_sma": window_sma,
            "window_ema": window_ema,
            "window_rsi": window_rsi
        }, start_time, False, error=error_msg)
        return f"Error: {error_msg}"

@tool("get_events")
def get_events_tool(symbol: str) -> str:
    """
    Detect market events like gaps, volatility spikes, and 52-week extremes.
    
    YOU MUST CALL THIS TOOL to detect market events. Do not guess or infer events without calling this tool.
    
    Args:
        symbol: Stock ticker symbol (e.g., 'AAPL', 'NVDA', 'TSLA')
    
    Returns:
        Formatted list of detected market events
    """
    start_time = time.time()
    try:
        json_result = detect_events(symbol)
        parsed_result = _parse_events(json_result)
        _log_tool_call("get_events", {"symbol": symbol}, start_time, True, parsed_result)
        return parsed_result
    except Exception as e:
        error_msg = str(e)
        _log_tool_call("get_events", {"symbol": symbol}, start_time, False, error=error_msg)
        return f"Error: {error_msg}"

def create_explanation_tool(gemini_api_key: str = ""):
    """
    Factory function to create an explanation tool with a specific Gemini API key.

    This tool automatically fetches indicators and events data from the MCP server.
    Agents only need to provide the symbol - all calculations happen in MCP.

    Args:
        gemini_api_key: Gemini API key for LLM explanations

    Returns:
        A tool function decorated with @tool
    """
    @tool("get_explanation")
    def get_explanation_tool(
        symbol: str,
        language: str = "en",
        tone: str = "neutral",
        risk_profile: str = "balanced",
        horizon_days: int = 30,
        bullets: bool = True
    ) -> str:
        """
        Get AI-powered explanation of technical analysis with market context.
        
        This tool automatically fetches all required data (indicators and events) from the MCP server.
        You only need to provide the stock symbol - all calculations are handled by MCP.
        
        Args:
            symbol: Stock ticker symbol (e.g., 'AAPL', 'NVDA', 'TSLA')
            language: Language code for explanation ('en' or 'he', default: 'en')
            tone: Tone of explanation ('neutral', 'concise', 'educational', 'headline', default: 'neutral')
            risk_profile: Risk profile ('cautious', 'balanced', 'aggressive', default: 'balanced')
            horizon_days: Investment horizon in days (default: 30)
            bullets: Whether to return bullet points (default: True)
        
        Returns:
            AI-generated technical analysis explanation
        """
        start_time = time.time()
        try:
            json_result = explain(symbol, language, tone, risk_profile, horizon_days, bullets, gemini_api_key)
            
            try:
                data = json.loads(json_result)
                if "error" in data:
                    _log_tool_call("get_explanation", {
                        "symbol": symbol,
                        "language": language,
                        "tone": tone
                    }, start_time, False, error=data.get("message", "Unknown error"))
                    return f"Error: {data.get('message', 'Unknown error')}"
                
                if isinstance(data, dict):
                    text = data.get("text", "")
                    rationale = data.get("rationale", [])
                    disclaimers = data.get("disclaimers", "")
                    
                    result = text
                    if rationale:
                        result += f"\n\nRationale:\n" + "\n".join(f"  • {r}" for r in rationale)
                    if disclaimers:
                        result += f"\n\n{disclaimers}"
                    
                    _log_tool_call("get_explanation", {
                        "symbol": symbol,
                        "language": language,
                        "tone": tone
                    }, start_time, True, result)
                    return result
                else:
                    _log_tool_call("get_explanation", {
                        "symbol": symbol,
                        "language": language,
                        "tone": tone
                    }, start_time, True, json_result)
                    return json_result
            except Exception:
                _log_tool_call("get_explanation", {
                    "symbol": symbol,
                    "language": language,
                    "tone": tone
                }, start_time, True, json_result)
                return json_result
        except Exception as e:
            error_msg = str(e)
            _log_tool_call("get_explanation", {
                "symbol": symbol,
                "language": language,
                "tone": tone
            }, start_time, False, error=error_msg)
            return f"Error: {error_msg}"
    
    return get_explanation_tool

TOOL_REGISTRY = {
    "search_symbols": search_symbols_tool,
    "get_quote": get_quote_tool,
    "get_price_series": get_price_series_tool,
    "get_indicators": get_indicators_tool,
    "get_events": get_events_tool,
}

def get_tools_by_names(tool_names: list, gemini_api_key: str = "") -> list:
    """
    Get tools by their names from the registry.

    Args:
        tool_names: List of tool names (e.g., ["search_symbols", "get_quote"])
        gemini_api_key: Gemini API key for explanation tool if needed

    Returns:
        List of tool functions
    """
    tools = []
    for tool_name in tool_names:
        if tool_name == "get_explanation":
            tools.append(create_explanation_tool(gemini_api_key))
        elif tool_name in TOOL_REGISTRY:
            tools.append(TOOL_REGISTRY[tool_name])
        else:
            print(f"Warning: Unknown tool '{tool_name}' skipped")
    return tools

def get_available_tools() -> list:
    """Get list of all available tool names"""
    return list(TOOL_REGISTRY.keys()) + ["get_explanation"]
