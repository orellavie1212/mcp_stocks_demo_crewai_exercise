# api.py
from __future__ import annotations

import json
import importlib
from typing import Any, Optional, Dict

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

# Optional: auto-load .env so you don't need --env-file .env
try:
    from dotenv import load_dotenv, find_dotenv  # type: ignore
    load_dotenv(find_dotenv(), override=False)
except Exception:
    pass

app = FastAPI(
    title="Stocks MCP Bridge",
    version="1.0.0",
    description="FastAPI bridge for MCP tools (Finnhub-backed): search, quote, series, indicators, events, explain.",
)

# Wide-open CORS for local dev / Streamlit
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------
# Utilities
# ---------------------------

def _tools():
    """Lazy import so the app can boot even if tools have a transient issue."""
    return importlib.import_module("mcp_server")

def _ok(payload: Any) -> Any:
    """
    Tools may return JSON strings. Normalize to Python objects.
    If it's plain text, wrap it under {"text": "..."}.
    """
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

# ---------------------------
# Schemas (request bodies)
# ---------------------------

class SearchBody(BaseModel):
    q: str = Field(..., min_length=1, description="Company name or ticker text")

class QuoteBody(BaseModel):
    symbol: str = Field(..., min_length=1)

class SeriesBody(BaseModel):
    symbol: str = Field(..., min_length=1)
    interval: str = Field("daily")
    lookback: int = Field(180, ge=1, le=5000)

class IndicatorsBody(BaseModel):
    symbol: str = Field(..., min_length=1)
    window_sma: int = Field(20, ge=2, le=500)
    window_ema: int = Field(50, ge=2, le=500)
    window_rsi: int = Field(14, ge=2, le=200)

class EventsBody(BaseModel):
    symbol: str = Field(..., min_length=1)


class ExplainBody(BaseModel):
    symbol: str = Field(..., min_length=1)
    language: str = Field("en", description="Two-letter code: 'en' or 'he'")
    tone: str = Field("neutral", description="neutral | concise | educational | headline")
    risk_profile: str = Field("balanced", description="cautious | balanced | aggressive")
    horizon_days: int = Field(30, ge=5, le=365)
    bullets: bool = Field(True, description="If true, return bullet points")
    openai_api_key: str = Field("", description="OpenAI API key for LLM explanations")

class BundleBody(BaseModel):
    """Fetch price series + indicators + events in one shot."""
    symbol: str = Field(..., min_length=1)
    lookback: int = Field(180, ge=1, le=5000)
    window_sma: int = Field(20, ge=2, le=500)
    window_ema: int = Field(50, ge=2, le=500)
    window_rsi: int = Field(14, ge=2, le=200)
    openai_api_key: str = Field("", description="OpenAI API key for LLM explanations")

# ---------------------------
# Health
# ---------------------------

@app.get("/health")
def health():
    return {"status": "ok"}

# ---------------------------
# Routes (robust handlers)
# ---------------------------

@app.post("/search")
async def route_search(body: SearchBody):
    try:
        t = _tools()
        out = t.search_symbols(body.q)
        return _ok(out)
    except ValidationError as ve:
        return _error("validation_error", ve.json(), status=422)
    except Exception as e:
        return _error("search_failed", str(e), status=500)

@app.post("/quote")
async def route_quote(body: QuoteBody):
    try:
        t = _tools()
        out = t.latest_quote(body.symbol)
        return _ok(out)
    except ValidationError as ve:
        return _error("validation_error", ve.json(), status=422)
    except Exception as e:
        return _error("quote_failed", str(e), status=500)

@app.post("/series")
async def route_series(body: SeriesBody):
    try:
        t = _tools()
        out = t.price_series(body.symbol, body.interval, body.lookback)
        return _ok(out)
    except ValidationError as ve:
        return _error("validation_error", ve.json(), status=422)
    except Exception as e:
        return _error("series_failed", str(e), status=500)

@app.post("/indicators")
async def route_indicators(body: IndicatorsBody):
    try:
        t = _tools()
        out = t.indicators(body.symbol, body.window_sma, body.window_ema, body.window_rsi)
        return _ok(out)
    except ValidationError as ve:
        return _error("validation_error", ve.json(), status=422)
    except Exception as e:
        return _error("indicators_failed", str(e), status=500)

@app.post("/events")
async def route_events(body: EventsBody):
    try:
        t = _tools()
        out = t.detect_events(body.symbol)
        return _ok(out)
    except ValidationError as ve:
        return _error("validation_error", ve.json(), status=422)
    except Exception as e:
        return _error("events_failed", str(e), status=500)


@app.post("/explain")
async def route_explain(body: ExplainBody):
    try:
        t = _tools()
        out = t.explain(
            body.symbol, 
            body.language, 
            body.tone, 
            body.risk_profile, 
            body.horizon_days, 
            body.bullets,
            body.openai_api_key
        )
        return _ok(out)
    except ValidationError as ve:
        return _error("validation_error", ve.json(), status=422)
    except Exception as e:
        return _error("explain_failed", str(e), status=500)

@app.post("/bundle")
async def route_bundle(body: BundleBody):
    """Convenience: return {"series": [...], "indicators": {...}, "events": {...}, "explain": {...}}"""
    try:
        t = _tools()
        series = _ok(t.price_series(body.symbol, "daily", body.lookback))
        indicators = _ok(t.indicators(body.symbol, body.window_sma, body.window_ema, body.window_rsi))
        events = _ok(t.detect_events(body.symbol))
        explain = _ok(t.explain(body.symbol, openai_api_key=body.openai_api_key))
        return {"series": series, "indicators": indicators, "events": events, "explain": explain}
    except ValidationError as ve:
        return _error("validation_error", ve.json(), status=422)
    except Exception as e:
        return _error("bundle_failed", str(e), status=500)
