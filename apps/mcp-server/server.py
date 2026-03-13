"""
MCP Server — Production version.

Teaching note (compare with original mcp_server.py):
  WHAT CHANGED:
  1. No CrewAI imports — the MCP server is now a pure data/tool service.
     It doesn't know anything about agents. This is separation of concerns.
  2. Input validation on every endpoint via Pydantic.
  3. Structured JSON logging with trace_id correlation.
  4. Internal auth token — only the agent-runtime can call this service.
  5. /health and /ready endpoints for GCP load balancer health checks.
  6. The datasource is imported from the same file as before (unchanged logic).

  WHY these changes matter:
  - The MCP server can be updated without touching the agent code.
  - Tool errors are observable (logged with trace_id, visible in Cloud Logging).
  - Any service can call it (not just Python agents — could be a Go service later).
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Any, Optional

import numpy as np
import pandas as pd
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Bootstrap: add project root and packages to path
# ---------------------------------------------------------------------------
_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.join(_here, "..", "..")
for _p in [_root, os.path.join(_root, "packages", "shared-config"),
           os.path.join(_root, "packages", "shared-observability")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from config import get_settings
from observability import setup_logging, CorrelatedLogger, set_correlation, get_trace_id

settings = get_settings()
_root_logger = setup_logging(
    service_name="mcp-server",
    log_level=settings.log_level,
    log_format=settings.log_format,
)
log = CorrelatedLogger("mcp-server")

# ---------------------------------------------------------------------------
# Datasource (same logic as original — yfinance backed)
# ---------------------------------------------------------------------------
# We import from the original datasource.py that lives at the repo root.
# In production, this file is copied into the container at build time.
_demo_root = os.path.join(_here, "..", "..")
if _demo_root not in sys.path:
    sys.path.insert(0, _demo_root)

from datasource import (  # noqa: E402
    search_symbols as ds_search,
    latest_quote as ds_quote,
    price_series as ds_series,
)

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Stock MCP Server",
    version="2.0.0",
    description=(
        "Production MCP server for stock analysis tools. "
        "Exposes: search_symbols, latest_quote, price_series, "
        "indicators, detect_events, explain."
    ),
    docs_url="/docs",
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict in production via ingress
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Internal auth middleware
# Teaching note: In production, use Cloud IAM + Workload Identity instead.
# This simple token auth is for the classroom demo.
# ---------------------------------------------------------------------------

@app.middleware("http")
async def auth_and_trace_middleware(request: Request, call_next):
    """
    1. Skip auth for health endpoints.
    2. Validate internal token for all other endpoints.
    3. Inject trace_id from header (or generate new one).
    """
    # Always allow health/ready
    if request.url.path in ("/health", "/ready", "/docs", "/openapi.json"):
        return await call_next(request)

    # Extract trace_id from header (set by job-api when forwarding requests)
    trace_id = request.headers.get("X-Trace-ID", "")
    job_id = request.headers.get("X-Job-ID", "")
    set_correlation(trace_id=trace_id, job_id=job_id)

    # Validate internal token (if auth enabled)
    if settings.api_auth_enabled:
        token = request.headers.get("X-Internal-Token", "")
        if token != settings.internal_api_token:
            log.warning("Unauthorized request to MCP server", path=str(request.url.path))
            return JSONResponse({"error": "unauthorized"}, status_code=401)

    return await call_next(request)


# ---------------------------------------------------------------------------
# Health endpoints (required by GCP load balancer)
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    """Liveness probe — is the container alive?"""
    return {"status": "ok", "service": "mcp-server"}


@app.get("/ready")
async def ready():
    """Readiness probe — is the service ready to accept traffic?"""
    try:
        # Quick check: can we import datasource?
        ds_quote("AAPL")
        return {"status": "ready"}
    except Exception as e:
        return JSONResponse({"status": "not_ready", "error": str(e)}, status_code=503)


# ---------------------------------------------------------------------------
# Technical indicators (same logic, now a proper utility module)
# ---------------------------------------------------------------------------

def calc_sma(s: pd.Series, w: int = 20) -> pd.Series:
    return s.rolling(w, min_periods=max(3, w // 2)).mean()

def calc_ema(s: pd.Series, w: int = 20) -> pd.Series:
    return s.ewm(span=w, adjust=False).mean()

def calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0.0)
    down = -delta.clip(upper=0.0)
    ma_up = up.ewm(alpha=1 / period, adjust=False).mean()
    ma_down = down.ewm(alpha=1 / period, adjust=False).mean()
    rs = ma_up / ma_down.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def flag_gaps(df: pd.DataFrame, threshold: float = 0.03) -> pd.DataFrame:
    prev_close = df["close"].shift(1)
    gap = (df["open"] - prev_close) / prev_close
    df = df.copy()
    df["gap_up"] = gap >= threshold
    df["gap_down"] = gap <= -threshold
    return df

def flag_volatility(df: pd.DataFrame, window: int = 20, mult: float = 2.0) -> pd.DataFrame:
    ret = df["close"].pct_change()
    vol = ret.rolling(window, min_periods=5).std()
    df = df.copy()
    df["vol_spike"] = ret.abs() > (mult * vol)
    return df

def flag_52w_extremes(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    roll_max = df["close"].rolling(252, min_periods=30).max()
    roll_min = df["close"].rolling(252, min_periods=30).min()
    df["is_52w_high"] = df["close"] >= roll_max
    df["is_52w_low"] = df["close"] <= roll_min
    return df

def _coerce_close(df: pd.DataFrame) -> pd.Series:
    if df is None or df.empty or "close" not in df.columns:
        return pd.Series(dtype="float64")
    return pd.to_numeric(df["close"], errors="coerce").dropna()


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class SearchRequest(BaseModel):
    q: str = Field(..., min_length=1, description="Company name or ticker")

class QuoteRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=10)

class SeriesRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=10)
    interval: str = Field(default="daily")
    lookback: int = Field(default=180, ge=1, le=5000)

class IndicatorsRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=10)
    window_sma: int = Field(default=20, ge=2, le=500)
    window_ema: int = Field(default=50, ge=2, le=500)
    window_rsi: int = Field(default=14, ge=2, le=200)

class EventsRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=10)

class ExplainRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=10)
    language: str = Field(default="en")
    tone: str = Field(default="neutral")
    risk_profile: str = Field(default="balanced")
    horizon_days: int = Field(default=30, ge=5, le=365)
    bullets: bool = Field(default=True)


# ---------------------------------------------------------------------------
# Tool implementations (instrument with logging)
# ---------------------------------------------------------------------------

def _timed_tool(tool_name: str, symbol: str, fn, *args, **kwargs) -> Any:
    """Execute a tool function with timing and logging."""
    start = time.perf_counter()
    try:
        result = fn(*args, **kwargs)
        duration_ms = (time.perf_counter() - start) * 1000
        log.info(
            f"Tool executed successfully",
            tool_name=tool_name,
            symbol=symbol,
            duration_ms=round(duration_ms, 1),
        )
        return result
    except Exception as e:
        duration_ms = (time.perf_counter() - start) * 1000
        log.error(
            f"Tool failed: {e}",
            tool_name=tool_name,
            symbol=symbol,
            duration_ms=round(duration_ms, 1),
        )
        raise


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

@app.post("/search")
async def route_search(body: SearchRequest):
    """Search for stock symbols by name or ticker."""
    start = time.perf_counter()
    try:
        result = ds_search(body.q)
        log.info("search_symbols called", query=body.q, result_count=len(result))
        return result
    except Exception as e:
        log.error(f"search_symbols failed: {e}", query=body.q)
        return JSONResponse(
            {"error": "search_failed", "message": str(e)}, status_code=500
        )


@app.post("/quote")
async def route_quote(body: QuoteRequest):
    """Get latest price, change %, volume for a symbol."""
    try:
        result = _timed_tool("latest_quote", body.symbol, ds_quote, body.symbol)
        return result
    except Exception as e:
        return JSONResponse(
            {"symbol": body.symbol, "error": "quote_failed", "message": str(e)},
            status_code=500,
        )


@app.post("/series")
async def route_series(body: SeriesRequest):
    """Get OHLCV price series."""
    try:
        df = _timed_tool("price_series", body.symbol, ds_series,
                          body.symbol, body.interval, body.lookback)
        for col in ["date", "open", "high", "low", "close", "volume"]:
            if col not in df.columns:
                df[col] = pd.Series(
                    dtype="float64" if col != "date" else "datetime64[ns]"
                )
        return json.loads(df.to_json(orient="records", date_format="iso"))
    except Exception as e:
        log.error(f"price_series failed: {e}", symbol=body.symbol)
        return JSONResponse(
            {"symbol": body.symbol, "error": "series_failed", "message": str(e)},
            status_code=500,
        )


@app.post("/indicators")
async def route_indicators(body: IndicatorsRequest):
    """Calculate SMA, EMA, RSI technical indicators."""
    try:
        def _calc():
            df = ds_series(body.symbol, "daily", 300)
            close = _coerce_close(df)
            if close.empty:
                return {"symbol": body.symbol, "error": "no_data"}
            return {
                "symbol": body.symbol,
                "last_close": float(close.iloc[-1]),
                "sma": float(calc_sma(close, body.window_sma).iloc[-1]),
                "ema": float(calc_ema(close, body.window_ema).iloc[-1]),
                "rsi": float(calc_rsi(close, body.window_rsi).iloc[-1]),
            }
        return _timed_tool("indicators", body.symbol, _calc)
    except Exception as e:
        log.error(f"indicators failed: {e}", symbol=body.symbol)
        return JSONResponse(
            {"symbol": body.symbol, "error": "indicators_failed", "message": str(e)},
            status_code=500,
        )


@app.post("/events")
async def route_events(body: EventsRequest):
    """Detect gap up/down, volatility spikes, 52-week extremes."""
    try:
        def _detect():
            df = ds_series(body.symbol, "daily", 400)
            if df is None or df.empty:
                return {"symbol": body.symbol, "error": "no_data"}
            for c in ["open", "high", "low", "close", "volume"]:
                if c in df.columns:
                    df[c] = pd.to_numeric(df[c], errors="coerce")
            df = df.dropna(subset=["open", "close"]).reset_index(drop=True)
            if df.empty:
                return {"symbol": body.symbol, "error": "no_valid_data"}
            df = flag_gaps(df)
            df = flag_volatility(df)
            df = flag_52w_extremes(df)
            last = df.iloc[-1]
            return {
                "symbol": body.symbol,
                "date": str(pd.to_datetime(last["date"]).date()) if "date" in df.columns else None,
                "gap_up": bool(last.get("gap_up", False)),
                "gap_down": bool(last.get("gap_down", False)),
                "vol_spike": bool(last.get("vol_spike", False)),
                "is_52w_high": bool(last.get("is_52w_high", False)),
                "is_52w_low": bool(last.get("is_52w_low", False)),
            }
        return _timed_tool("detect_events", body.symbol, _detect)
    except Exception as e:
        log.error(f"detect_events failed: {e}", symbol=body.symbol)
        return JSONResponse(
            {"symbol": body.symbol, "error": "events_failed", "message": str(e)},
            status_code=500,
        )


@app.post("/explain")
async def route_explain(body: ExplainRequest):
    """
    AI-powered technical analysis explanation using Gemini.

    Teaching note:
      The MCP server calls Gemini here (not the agent).
      The agent runtime routes the 'explain' tool call to this endpoint.
      This keeps LLM logic in one place and makes it observable.
    """
    try:
        # Gather indicators + events (same as before)
        indicators_resp = await route_indicators(
            IndicatorsRequest(symbol=body.symbol)
        )
        events_resp = await route_events(EventsRequest(symbol=body.symbol))

        ind = indicators_resp if isinstance(indicators_resp, dict) else {}
        evt = events_resp if isinstance(events_resp, dict) else {}

        # Call Gemini (via shared-config factory)
        from config import get_llm
        llm = get_llm("main")

        system_prompt = (
            "You are an impartial market analyst. Summarize technical signals clearly. "
            "Avoid predictions and financial advice. Use short, concrete language. "
            f"Tone: {body.tone}. Risk profile: {body.risk_profile}. "
            f"Language: {body.language}. Horizon: {body.horizon_days} days."
        )

        user_prompt = (
            f"Generate a technical analysis summary for {body.symbol}.\n"
            f"Indicators: {json.dumps(ind)}\n"
            f"Events: {json.dumps(evt)}\n"
            f"Instructions: Keep it under 120 words. "
            f"{'Use bullet points.' if body.bullets else 'Use prose.'} "
            "No price targets. No investment advice."
        )

        from langchain_core.messages import HumanMessage, SystemMessage
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]

        response = llm.invoke(messages)
        text = response.content if hasattr(response, "content") else str(response)

        log.info("explain called", symbol=body.symbol, language=body.language)
        return {
            "text": text,
            "disclaimers": "Not investment advice. For educational purposes only.",
            "symbol": body.symbol,
        }
    except Exception as e:
        log.error(f"explain failed: {e}", symbol=body.symbol)
        return JSONResponse(
            {"symbol": body.symbol, "error": "explain_failed", "message": str(e)},
            status_code=500,
        )


@app.post("/bundle")
async def route_bundle(symbol: str, lookback: int = 180):
    """Convenience: fetch series + indicators + events in one shot."""
    series = await route_series(SeriesRequest(symbol=symbol, lookback=lookback))
    indicators = await route_indicators(IndicatorsRequest(symbol=symbol))
    events = await route_events(EventsRequest(symbol=symbol))
    return {"series": series, "indicators": indicators, "events": events}


# ---------------------------------------------------------------------------
# MCP server (for native MCP protocol clients)
# ---------------------------------------------------------------------------
mcp = FastMCP("stocks-analyzer")

@mcp.tool()
def search_symbols(query: str) -> str:
    """Symbol lookup by company name/ticker. Returns a JSON array."""
    try:
        return json.dumps(ds_search(query), ensure_ascii=False)
    except Exception as e:
        return json.dumps([{"error": "search_failed", "message": str(e)}])

@mcp.tool()
def latest_quote(symbol: str) -> str:
    """Latest price, change %, volume."""
    try:
        return json.dumps(ds_quote(symbol), ensure_ascii=False)
    except Exception as e:
        return json.dumps({"symbol": symbol, "error": "quote_failed", "message": str(e)})

@mcp.tool()
def price_series(symbol: str, interval: str = "daily", lookback: int = 180) -> str:
    """OHLCV series as a JSON array."""
    try:
        df = ds_series(symbol, interval, lookback)
        return df.to_json(orient="records", date_format="iso")
    except Exception as e:
        return json.dumps({"symbol": symbol, "error": "series_failed", "message": str(e)})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=8001,
        reload=settings.is_local,
        log_level=settings.log_level.lower(),
    )
