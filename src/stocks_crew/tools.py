# tools.py — the five stock-analysis tools, as plain functions.
#
# These are wrapped in @mcp.tool() by mcp_server.py and called directly (in
# process) by api.py's data routes. Keeping them free of MCP decorators means
# both callers get the same code path and the functions stay trivially testable.
#
# The agent-facing contract -- full Args/Returns, i.e. the text the model reads
# when choosing a tool -- lives on the decorated wrappers in mcp_server.py. The
# docstrings here are for whoever calls these functions from Python.
#
# Every function returns a JSON *string*, and reports failure as a JSON object
# {"error": "<code>", "message": "..."} rather than raising -- an agent on the
# other end of MCP can read that and react, whereas an exception just dies.
#
# NOTE: nothing here may print to stdout. Under the stdio MCP transport, stdout
# is the JSON-RPC channel; a stray print corrupts the protocol.
from __future__ import annotations

import json
from typing import Any

import pandas as pd

from .analytics import (
    calc_ema,
    calc_rsi,
    calc_sma,
    coerce_close,
    flag_52w_extremes,
    flag_gaps,
    flag_volatility,
)
from . import _paths  # noqa: F401  (puts packages/shared-* on sys.path)
from datasource import latest_quote as ds_quote  # noqa: E402  (packages/shared-datasource)
from datasource import price_series as ds_series  # noqa: E402
from datasource import search_symbols as ds_search  # noqa: E402


#: Decimal places kept in numeric tool output. yfinance returns float64 values
#: like 343.459991455078; passing all 15 digits through makes the agents quote
#: noise ("closed at 343.459991455078") and wastes context on a long series.
#: Four places is finer than any displayed price or percentage.
DECIMALS = 4


def _rounded(value: Any) -> Any:
    """Round floats inside a dict/list so tool output carries sensible digits."""
    if isinstance(value, float):
        return round(value, DECIMALS)
    if isinstance(value, dict):
        return {k: _rounded(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_rounded(v) for v in value]
    return value


def search_symbols(query: str) -> str:
    """Look up ticker symbols by company name or partial ticker.

    Returns a JSON array of {symbol, name, region, currency}.
    """
    try:
        return json.dumps(ds_search(query), ensure_ascii=False)
    except Exception as e:
        return json.dumps([{"error": "search_failed", "message": str(e)}])


def latest_quote(symbol: str) -> str:
    """Get the latest price, absolute/percent change, and volume for a symbol.

    Returns a JSON object: symbol, price, change, change_percent, volume, timestamp.
    """
    try:
        return json.dumps(_rounded(ds_quote(symbol)), ensure_ascii=False)
    except Exception as e:
        return json.dumps({"symbol": symbol, "error": "quote_failed", "message": str(e)})


def price_series(symbol: str, interval: str = "daily", lookback: int = 180) -> str:
    """Get the historical OHLCV price series for a symbol.

    Only `interval="daily"` is supported today. Returns a JSON array of
    {date, open, high, low, close, volume} rows, dates in ISO format.
    """
    try:
        df = ds_series(symbol, interval, lookback)
        # Guarantee expected columns even if empty
        for col in ["date", "open", "high", "low", "close", "volume"]:
            if col not in df.columns:
                df[col] = pd.Series(dtype="float64" if col != "date" else "datetime64[ns]")
        return df.round(DECIMALS).to_json(orient="records", date_format="iso")
    except Exception as e:
        return json.dumps({"symbol": symbol, "error": "series_failed", "message": str(e)})


def indicators(
    symbol: str,
    window_sma: int = 20,
    window_ema: int = 50,
    window_rsi: int = 14,
) -> str:
    """Compute the current technical indicator snapshot for a symbol.

    Returns a JSON object: symbol, last_close, sma, ema, rsi. Any indicator with
    insufficient history comes back as null.
    """
    try:
        df = ds_series(symbol, "daily", 300)
        close = coerce_close(df)
        if close.empty:
            return json.dumps({
                "symbol": symbol,
                "error": "no_data",
                "message": f"No data available for symbol {symbol}",
            })
        sma = calc_sma(close, window_sma).iloc[-1]
        ema = calc_ema(close, window_ema).iloc[-1]
        rsi = calc_rsi(close, window_rsi).iloc[-1]
        out = {
            "symbol": symbol,
            "last_close": round(float(close.iloc[-1]), DECIMALS),
            "sma": round(float(sma), DECIMALS) if pd.notna(sma) else None,
            "ema": round(float(ema), DECIMALS) if pd.notna(ema) else None,
            "rsi": round(float(rsi), DECIMALS) if pd.notna(rsi) else None,
        }
        return json.dumps(out)
    except Exception as e:
        return json.dumps({"symbol": symbol, "error": "indicators_failed", "message": str(e)})


def detect_events(symbol: str) -> str:
    """Detect notable technical events on the most recent trading day.

    Checks for price gaps (>=3% vs. the previous close), volatility spikes
    (a daily move beyond 2x the 20-day rolling volatility), and 52-week extremes.
    Returns a JSON object: symbol, date, and the boolean flags gap_up, gap_down,
    vol_spike, is_52w_high, is_52w_low.
    """
    try:
        df = ds_series(symbol, "daily", 400)
        if df is None or df.empty:
            return json.dumps({
                "symbol": symbol,
                "error": "no_data",
                "message": f"No data available for symbol {symbol}",
            })
        # ensure numeric
        for c in ["open", "high", "low", "close", "volume"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=["open", "close"]).reset_index(drop=True)
        if df.empty:
            return json.dumps({
                "symbol": symbol,
                "error": "no_data",
                "message": f"No valid data available for symbol {symbol}",
            })

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


#: The functions mcp_server.py exposes over MCP, in registration order. The
#: registration itself is explicit there (one @mcp.tool() per function); this
#: tuple is just the inventory, used by the tests.
ALL_TOOLS = (search_symbols, latest_quote, price_series, indicators, detect_events)
