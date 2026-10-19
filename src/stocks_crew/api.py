# api.py -- optional FastAPI bridge over the same five tools the MCP server serves.
#
#     uvicorn stocks_crew.api:app --host 127.0.0.1 --port 8001
#
# The crew does NOT need this: it reaches the tools over stdio MCP (crew.py).
# This is for poking at the data layer with curl or /docs, and it calls the same
# tools.py functions the MCP server wraps, in process.
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from . import _paths  # noqa: F401  (puts packages/shared-* on sys.path)
from models import validate_symbol  # noqa: E402
from . import tools

try:
    from dotenv import find_dotenv, load_dotenv  # type: ignore

    load_dotenv(find_dotenv(), override=False)
except Exception:
    pass

app = FastAPI(
    title="Stocks MCP Bridge",
    version="1.0.0",
    description="FastAPI bridge over the stocks_crew tools: search, quote, series, indicators, events.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _ok(payload: Any) -> Any:
    """Tools return JSON strings. Normalize to Python objects."""
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except Exception:
            return {"text": payload}
    return payload


def _error(name: str, message: str, status: int = 500, extra: Optional[Dict[str, Any]] = None):
    data: Dict[str, Any] = {"error": name, "message": message}
    if extra:
        data.update(extra)
    return JSONResponse(data, status_code=status)


class _SymbolBody(BaseModel):
    symbol: str = Field(..., min_length=1)

    @field_validator("symbol")
    @classmethod
    def _check_symbol(cls, v: str) -> str:
        return validate_symbol(v)


class SearchBody(BaseModel):
    q: str = Field(..., min_length=1, description="Company name or ticker text")


class QuoteBody(_SymbolBody):
    pass


class SeriesBody(_SymbolBody):
    interval: str = Field("daily")
    lookback: int = Field(180, ge=1, le=5000)


class IndicatorsBody(_SymbolBody):
    window_sma: int = Field(20, ge=2, le=500)
    window_ema: int = Field(50, ge=2, le=500)
    window_rsi: int = Field(14, ge=2, le=200)


class EventsBody(_SymbolBody):
    pass


class BundleBody(_SymbolBody):
    """Fetch price series + indicators + events in one shot."""

    lookback: int = Field(180, ge=1, le=5000)
    window_sma: int = Field(20, ge=2, le=500)
    window_ema: int = Field(50, ge=2, le=500)
    window_rsi: int = Field(14, ge=2, le=200)


@app.get("/")
def root():
    """Root endpoint -- confirms the API is running."""
    return {
        "service": "MCP Stocks API",
        "status": "ok",
        "endpoints": ["/health", "/search", "/quote", "/series", "/indicators", "/events", "/bundle"],
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/search")
async def route_search(body: SearchBody):
    try:
        return _ok(tools.search_symbols(body.q))
    except Exception as e:
        return _error("search_failed", str(e))


@app.post("/quote")
async def route_quote(body: QuoteBody):
    try:
        return _ok(tools.latest_quote(body.symbol))
    except Exception as e:
        return _error("quote_failed", str(e))


@app.post("/series")
async def route_series(body: SeriesBody):
    try:
        return _ok(tools.price_series(body.symbol, body.interval, body.lookback))
    except Exception as e:
        return _error("series_failed", str(e))


@app.post("/indicators")
async def route_indicators(body: IndicatorsBody):
    try:
        return _ok(
            tools.indicators(body.symbol, body.window_sma, body.window_ema, body.window_rsi)
        )
    except Exception as e:
        return _error("indicators_failed", str(e))


@app.post("/events")
async def route_events(body: EventsBody):
    try:
        return _ok(tools.detect_events(body.symbol))
    except Exception as e:
        return _error("events_failed", str(e))


@app.post("/bundle")
async def route_bundle(body: BundleBody):
    """Convenience: {"series": [...], "indicators": {...}, "events": {...}}"""
    try:
        return {
            "series": _ok(tools.price_series(body.symbol, "daily", body.lookback)),
            "indicators": _ok(
                tools.indicators(body.symbol, body.window_sma, body.window_ema, body.window_rsi)
            ),
            "events": _ok(tools.detect_events(body.symbol)),
        }
    except Exception as e:
        return _error("bundle_failed", str(e))
