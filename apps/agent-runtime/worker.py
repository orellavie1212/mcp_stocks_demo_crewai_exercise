"""
Agent Runtime Worker — Production version.

Teaching note:
  This is the heart of the production system. Compare with the original agents.py:

  WHAT CHANGED:
  1. Runs as a Pub/Sub subscriber — pulls messages from the queue.
     No longer called directly from the UI.
  2. Calls the MCP server via HTTP (not via direct Python import).
     This means: MCP server updates don't require restarting the agent.
  3. Uses Gemini via Vertex AI or Google AI Studio (not OpenAI).
  4. Guardrails at input, tool, and output layers.
  5. Redis caching — if the same query ran before, return cached result.
  6. Job status written to Firestore as it progresses.
  7. Langfuse callback auto-traces every LLM call.
  8. Retry with exponential backoff and dead-letter queue.

  WHY stateless workers?
    Each worker pod processes one message at a time, independently.
    If it crashes, Pub/Sub redelivers the message.
    GKE HPA scales the number of worker pods based on queue depth.
"""
from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
import base64
import hashlib
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:  # only for the annotation; the real import is lazy (see __aenter__)
    from mcp import ClientSession

import httpx
from crewai import Agent, Crew, Process, Task

_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.join(_here, "..", "..")
for _p in [
    _root,
    os.path.join(_root, "packages", "shared-config"),
    os.path.join(_root, "packages", "shared-observability"),
    os.path.join(_root, "packages", "shared-models"),
    os.path.join(_root, "packages", "shared-guardrails"),
]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from config import get_settings, get_llm
from observability import (
    setup_logging, CorrelatedLogger, LangfuseTracer,
    set_correlation, get_trace_id, get_job_id, estimate_cost,
)
from tool_tracer import ToolTracer

# Langfuse decorators work without any LangChain dependency.
# langfuse.callback.CallbackHandler is broken with langchain 1.x (missing
# langchain.callbacks.base + langchain.schema.agent). Use @observe instead.
try:
    from langfuse.decorators import observe as _langfuse_observe, langfuse_context as _lf_ctx
    _LANGFUSE_DECORATORS_OK = True
except ImportError:
    _LANGFUSE_DECORATORS_OK = False
from models import (
    JobRecord, JobStatus, ToolCallRecord, UsageRecord, GuardrailEvent,
    GuardrailLayer, GuardrailDecision, PubSubMessage,
)
from guardrails import GuardrailPipeline

settings = get_settings()
setup_logging("agent-runtime", settings.log_level, settings.log_format)
log = CorrelatedLogger("agent-runtime")
tracer = LangfuseTracer(enabled=settings.langfuse_enabled)


#: Tool names the mcp-server exposes over real MCP (Streamable HTTP), matching
#: its @mcp.tool() functions exactly (see apps/mcp-server/server.py). "explain"
#: is deliberately not here — it calls an LLM directly and sits above MCP, not
#: as an MCP data tool (same design note as upstream/main's mcp_server.py).
_MCP_TOOL_NAMES = {"search_symbols", "latest_quote", "price_series", "indicators", "detect_events"}


class MCPClient:
    """
    Real MCP client (Streamable HTTP transport) for the mcp-server's 5 data
    tools, plus a plain REST call for /explain.

    Teaching note:
      The previous version of this class called the MCP server's REST routes
      over plain httpx — decorated with MCP scaffolding that was never run.
      This version speaks real MCP JSON-RPC to the /mcp sub-app mounted by
      apps/mcp-server/server.py (mcp.streamable_http_app()).

      CrewAI 1.x runs tool functions as plain sync callables inside a
      ThreadPoolExecutor, while process_job() itself runs on the main asyncio
      event loop. The mcp SDK's ClientSession is async-only, and opening a
      *second* event loop inside that worker thread (asyncio.run()) is what
      broke the old REST-based sync client (see the exception it used to
      raise: "asyncio.run() cannot be called from a running event loop").
      So instead of opening a fresh connection per call, one MCPClient opens a
      single ClientSession on the main loop (__aenter__, before crew.kickoff())
      and each sync tool callback hands its call to that same loop via
      asyncio.run_coroutine_threadsafe() — the supported way to invoke
      coroutine-based code from a different thread without touching the event
      loop machinery itself.
    """

    def __init__(self, base_url: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        # Trailing slash matches the mcp-server's mount exactly (Starlette
        # otherwise 307-redirects "/mcp" -> "/mcp/" — httpx follows that by
        # default so it would still work, just with an avoidable round trip).
        self.mcp_url = f"{self.base_url}/mcp/"
        self.timeout = timeout
        self._tool_trace: List[ToolCallRecord] = []
        self._session: Optional["ClientSession"] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._exit_stack: Optional[AsyncExitStack] = None

    def _make_headers(self) -> Dict[str, str]:
        """Pass correlation IDs to the MCP server for end-to-end tracing."""
        return {
            "X-Trace-ID": get_trace_id(),
            "X-Job-ID": get_job_id(),
            "X-Internal-Token": settings.internal_api_token,
        }

    async def __aenter__(self) -> "MCPClient":
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        self._loop = asyncio.get_running_loop()
        self._exit_stack = AsyncExitStack()
        read, write, _ = await self._exit_stack.enter_async_context(
            streamablehttp_client(
                self.mcp_url,
                headers=self._make_headers(),
                timeout=self.timeout,
            )
        )
        self._session = await self._exit_stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        return self

    async def __aexit__(self, *exc_info) -> None:
        if self._exit_stack is not None:
            await self._exit_stack.aclose()
        self._session = None
        self._exit_stack = None

    def _record(self, tool: str, payload: Dict[str, Any], start: float, result: Any = None, error: Optional[str] = None) -> None:
        duration_ms = (time.perf_counter() - start) * 1000
        record = ToolCallRecord(
            tool_name=tool,
            arguments=payload,
            success=error is None,
            duration_ms=round(duration_ms, 1),
            result_preview=str(result)[:300] if result is not None else "",
            error=error,
        )
        self._tool_trace.append(record)
        if error is None:
            log.info("MCP tool call succeeded", tool_name=tool, duration_ms=record.duration_ms)
        else:
            log.error(f"MCP tool call failed: {error}", tool_name=tool, duration_ms=duration_ms)

    def _call_mcp_tool_sync(self, tool: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Call a real MCP tool from a CrewAI tool callback (sync, worker thread)."""
        import json as _json

        start = time.perf_counter()
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._session.call_tool(tool, payload), self._loop
            )
            call_result = future.result(timeout=self.timeout)
            # Every tool in apps/mcp-server/server.py returns a single JSON
            # string in its first TextContent block (json.dumps(...)).
            text = "".join(
                block.text for block in call_result.content if getattr(block, "text", None)
            )
            result = _json.loads(text) if text else {}
            if call_result.isError:
                error = result.get("error") or result.get("message") or "MCP tool error"
                self._record(tool, payload, start, error=error)
                return {"error": error}
            self._record(tool, payload, start, result=result)
            return result
        except Exception as e:
            # concurrent.futures.TimeoutError's str() is '' — a blank error
            # message is a dead end when debugging a real timeout (e.g. many
            # crews hitting a single-process mcp-server concurrently in Lab
            # 2, which has no horizontal scaling the way Lab 3's Pub/Sub
            # workers do). Fall back to the exception's class name so
            # there's always something to go on.
            message = str(e) or f"{type(e).__name__} after {self.timeout}s"
            self._record(tool, payload, start, error=message)
            return {"error": message}

    def _call_rest_sync(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Plain REST call for endpoints with no MCP tool equivalent (/explain)."""
        start = time.perf_counter()
        with httpx.Client(timeout=self.timeout) as client:
            try:
                response = client.post(
                    f"{self.base_url}/{path}",
                    json=payload,
                    headers={**self._make_headers(), "Content-Type": "application/json"},
                )
                response.raise_for_status()
                result = response.json()
                self._record(path, payload, start, result=result)
                return result
            except Exception as e:
                self._record(path, payload, start, error=str(e))
                return {"error": str(e)}

    def call_sync(self, tool: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Dispatch to a real MCP tool call, or REST for /explain."""
        if tool in _MCP_TOOL_NAMES:
            return self._call_mcp_tool_sync(tool, payload)
        return self._call_rest_sync(tool, payload)

    @property
    def tool_trace(self) -> List[ToolCallRecord]:
        return list(self._tool_trace)

    def clear_trace(self):
        self._tool_trace.clear()


def make_crewai_tools(mcp: MCPClient, guardrail: GuardrailPipeline):
    """
    Create CrewAI tools that call the MCP server over HTTP.

    Teaching note:
      Each @tool function:
      1. Checks tool guardrails (Layer 2)
      2. Calls the MCP server via HTTP
      3. Returns formatted text for the agent to reason over
    """
    from crewai.tools import tool

    @tool("search_symbols")
    def search_symbols(q: str) -> str:
        """Search for stock symbols by company name or ticker."""
        guard = guardrail.check_tool_call("search_symbols", {"q": q})
        if not guard.allowed:
            return f"Tool blocked: {guard.reason}"
        result = mcp.call_sync("search_symbols", {"query": q})
        if isinstance(result, list):
            lines = [f"• {r.get('symbol')}: {r.get('name')}" for r in result[:5] if r.get('symbol')]
            return "\n".join(lines) if lines else "No results found."
        return str(result)

    @tool("latest_quote")
    def latest_quote(symbol: str) -> str:
        """Get the latest price, change %, and volume for a stock symbol."""
        guard = guardrail.check_tool_call("latest_quote", {"symbol": symbol})
        if not guard.allowed:
            return f"Tool blocked: {guard.reason}"
        r = mcp.call_sync("latest_quote", {"symbol": symbol.upper()})
        if r.get("error"):
            return f"Error: {r['error']}"
        price = r.get("price")
        change_pct = r.get("change_percent")
        volume = r.get("volume")
        sign = "+" if (change_pct or 0) >= 0 else ""
        return (
            f"Quote for {symbol.upper()}:\n"
            f"  Price: ${price:.2f}\n"
            f"  Change: {sign}{change_pct:.2f}%\n"
            f"  Volume: {int(volume or 0):,}"
        )

    @tool("price_series")
    def price_series(symbol: str) -> str:
        """Get historical OHLCV price data for a stock symbol."""
        guard = guardrail.check_tool_call("price_series", {"symbol": symbol})
        if not guard.allowed:
            return f"Tool blocked: {guard.reason}"
        result = mcp.call_sync("price_series", {"symbol": symbol.upper(), "lookback": 180})
        if isinstance(result, list) and result:
            closes = [float(r.get("close", 0)) for r in result if r.get("close")]
            if closes:
                change_pct = ((closes[-1] - closes[0]) / closes[0]) * 100
                sign = "+" if change_pct >= 0 else ""
                return (
                    f"Price Series ({len(closes)} days):\n"
                    f"  First: ${closes[0]:.2f}\n"
                    f"  Last:  ${closes[-1]:.2f}\n"
                    f"  High:  ${max(closes):.2f}\n"
                    f"  Low:   ${min(closes):.2f}\n"
                    f"  Change: {sign}{change_pct:.1f}%"
                )
        return "No price data available."

    @tool("indicators")
    def indicators(symbol: str) -> str:
        """Get SMA, EMA, RSI technical indicators for a stock symbol."""
        guard = guardrail.check_tool_call("indicators", {"symbol": symbol})
        if not guard.allowed:
            return f"Tool blocked: {guard.reason}"
        r = mcp.call_sync("indicators", {"symbol": symbol.upper()})
        if r.get("error"):
            return f"Error: {r['error']}"
        rsi = r.get("rsi") or 0
        rsi_label = " (Overbought)" if rsi > 70 else " (Oversold)" if rsi < 30 else ""
        return (
            f"Technical Indicators for {symbol.upper()}:\n"
            f"  Last Close: ${(r.get('last_close') or 0):.2f}\n"
            f"  SMA(20): ${(r.get('sma') or 0):.2f}\n"
            f"  EMA(50): ${(r.get('ema') or 0):.2f}\n"
            f"  RSI(14): {rsi:.1f}{rsi_label}"
        )

    @tool("detect_events")
    def detect_events(symbol: str) -> str:
        """Detect gap up/down, volatility spikes, 52-week extremes."""
        guard = guardrail.check_tool_call("detect_events", {"symbol": symbol})
        if not guard.allowed:
            return f"Tool blocked: {guard.reason}"
        r = mcp.call_sync("detect_events", {"symbol": symbol.upper()})
        if r.get("error"):
            return f"Error: {r['error']}"
        events = []
        if r.get("gap_up"): events.append("Gap Up")
        if r.get("gap_down"): events.append("Gap Down")
        if r.get("vol_spike"): events.append("Volatility Spike")
        if r.get("is_52w_high"): events.append("52-Week High")
        if r.get("is_52w_low"): events.append("52-Week Low")
        date = r.get("date", "today")
        return (
            f"Market Events for {symbol.upper()} ({date}):\n"
            f"  {', '.join(events) if events else 'No significant events detected'}"
        )

    @tool("explain")
    def explain(symbol: str, tone: str = "neutral") -> str:
        """Get an AI-powered technical analysis explanation for a stock."""
        guard = guardrail.check_tool_call("explain", {"symbol": symbol})
        if not guard.allowed:
            return f"Tool blocked: {guard.reason}"
        r = mcp.call_sync("explain", {"symbol": symbol.upper(), "tone": tone})
        if r.get("error"):
            return f"Error: {r['error']}"
        return r.get("text", "No explanation available.")

    return [search_symbols, latest_quote, price_series, indicators, detect_events, explain]


def _hallucination_guardrail(llm) -> Tuple[Optional[Any], Dict[str, Any]]:
    """CrewAI's own built-in output guardrail, attached to the report task
    as a teaching contrast against guardrails.check_output() below.

    READ THIS BEFORE TRUSTING IT: in the open-source crewai package,
    HallucinationGuardrail.__call__ is a placeholder — it returns
    (True, output) for every input and logs a link to the hosted platform.
    Only crewai's hosted product installs the real
    `_validate_output_hook` this class defers to. Handed a report that
    invents a number, it passes. That's exactly why guardrails.py's
    deterministic evidence-grounding check (fed by ToolTracer, run after
    kickoff) is the one actually verifying anything here — this is wired
    up so the gap between "attached" and "enforcing" is visible, not hidden.

    `enforcing` is detected rather than hardcoded: the placeholder's
    __call__ defers to a module-level `_validate_output_hook`, so "is that
    hook callable?" is the same question the class itself asks. If crewai
    ever ships the real check in open source, this starts reporting true
    without a code change here.
    """
    try:
        from crewai.tasks.hallucination_guardrail import HallucinationGuardrail
        from crewai.tasks import hallucination_guardrail as _hg_module
    except ImportError:
        return None, {
            "available": False,
            "enforcing": False,
            "note": "This crewai build has no HallucinationGuardrail.",
        }

    enforcing = callable(getattr(_hg_module, "_validate_output_hook", None))
    status = {
        "available": True,
        "enforcing": enforcing,
        "note": (
            "CrewAI's HallucinationGuardrail is active on the report task."
            if enforcing else
            "CrewAI's HallucinationGuardrail is attached to the report task "
            "but is a no-op in the open-source package — it passes every "
            "output. The 'grounding' check is what's actually verifying "
            "figures against tool evidence."
        ),
    }
    return HallucinationGuardrail(llm=llm), status


def build_crew(
    symbol: str,
    query: str,
    mcp: MCPClient,
    guardrail: GuardrailPipeline,
    lf_trace=None,
    provider: Optional[str] = None,
    api_key: str = "",
) -> Tuple[Crew, Dict[str, Any]]:
    """
    Build the 4-agent CrewAI crew.

    Teaching note:
      Same 4 agents as the demo, but now:
      - They use Gemini instead of OpenAI (by default — see provider/api_key)
      - Their tools call the MCP server over HTTP (not direct import)
      - The Langfuse callback auto-traces every LLM call
      - Model routing: all agents use MAIN tier (gemini-3.5-flash)
        except the report writer uses STRONG tier (gemini-3.1-pro-preview)

      Pass lf_trace so the CrewAI LLM spans are nested under the
      same job trace in Langfuse (visible as a single trace tree).

      provider/api_key come from the request (AnalysisRequest.provider,
      PubSubMessage.api_key) and override LLM_PROVIDER/.env for this run
      only — see get_llm()'s docstring in shared-config/config.py.
    """
    tools = make_crewai_tools(mcp, guardrail)
    research_tools = tools[:3]
    technical_tools = tools[3:]
    sector_tools = tools[:2] + [tools[3]]

    main_llm = get_llm("main", provider=provider, api_key=api_key)
    strong_llm = get_llm("strong", provider=provider, api_key=api_key)

    langfuse_cb = tracer.get_callback(trace=lf_trace)
    callbacks = [langfuse_cb] if langfuse_cb else []

    research_agent = Agent(
        role="Stock Research Specialist",
        goal="Gather comprehensive information about stocks using real tool data",
        backstory=(
            "You are an experienced stock researcher with deep knowledge of financial markets. "
            "You always call tools to get real data — you never guess or make up numbers."
        ),
        tools=research_tools,
        llm=main_llm,
        verbose=True,
        allow_delegation=False,
    )

    technical_agent = Agent(
        role="Technical Analysis Expert",
        goal="Perform technical analysis using indicators and market event data",
        backstory=(
            "You are a seasoned technical analyst with 15 years of experience. "
            "You interpret RSI, moving averages, and market events to assess momentum. "
            "You always cite specific numbers from tool outputs."
        ),
        tools=technical_tools,
        llm=main_llm,
        verbose=True,
        allow_delegation=False,
    )

    sector_agent = Agent(
        role="Sector Comparison Specialist",
        goal="Compare the target stock against 3-5 sector peers",
        backstory=(
            "You are a sector analyst specialising in comparative analysis. "
            "You identify peer companies and compare them on price, indicators, and fundamentals."
        ),
        tools=sector_tools,
        llm=main_llm,
        verbose=True,
        allow_delegation=False,
    )

    report_agent = Agent(
        role="Financial Report Writer",
        goal="Synthesize all research into a clear, structured investment report",
        backstory=(
            "You are a professional financial writer. You transform raw research data "
            "into clear, actionable reports. You never give investment advice — "
            "you present facts and analysis."
        ),
        tools=[],
        llm=strong_llm,
        verbose=True,
        allow_delegation=False,
    )

    research_task = Task(
        description=(
            f"Research the stock '{symbol}' to answer: '{query}'\n"
            "YOU MUST call search_symbols, latest_quote, and price_series in order. "
            "Reference actual values from each tool call in your output."
        ),
        expected_output="Structured research report with verified data from all 3 tool calls.",
        agent=research_agent,
        tools=research_tools,
    )

    technical_task = Task(
        description=(
            f"Perform technical analysis on '{symbol}'.\n"
            "Call indicators, detect_events, and explain. "
            "Report specific indicator values (e.g., 'RSI is 67.3 — approaching overbought')."
        ),
        expected_output="Technical analysis with specific indicator values and event detections.",
        agent=technical_agent,
        tools=technical_tools,
        context=[research_task],
    )

    sector_task = Task(
        description=(
            f"Identify 3-5 sector peers for '{symbol}' and compare them "
            "on price performance, RSI, and SMA. Use search_symbols to find peers."
        ),
        expected_output="Sector comparison table with data for target stock and 3-5 peers.",
        agent=sector_agent,
        tools=sector_tools,
        context=[research_task, technical_task],
    )

    hallucination_guardrail, hallucination_status = _hallucination_guardrail(strong_llm)

    report_task = Task(
        description=(
            f"Write a comprehensive analysis report for '{symbol}' "
            "synthesising the research, technical analysis, and sector comparison. "
            "Include: Executive Summary, Market Position, Technical Analysis, "
            "Sector Comparison, Risk Assessment, and Key Takeaways."
        ),
        expected_output="Professional investment analysis report (no financial advice).",
        agent=report_agent,
        context=[research_task, technical_task, sector_task],
        guardrail=hallucination_guardrail,
    )

    crew = Crew(
        agents=[research_agent, technical_agent, sector_agent, report_agent],
        tasks=[research_task, technical_task, sector_task, report_task],
        process=Process.sequential,
        verbose=True,
        callbacks=callbacks,
    )
    return crew, hallucination_status


def _cache_key(query: str, symbols: List[str]) -> str:
    """Generate a deterministic cache key from query + symbols."""
    content = f"{query.lower().strip()}|{','.join(sorted(s.upper() for s in symbols))}"
    return f"analysis:{hashlib.sha256(content.encode()).hexdigest()[:16]}"


async def get_cached_result(query: str, symbols: List[str]) -> Optional[str]:
    """Check Redis for a cached result. Returns None if not cached.

    Teaching note (Lab 2):
      socket_connect_timeout + socket_timeout cap the TCP connect and command
      round-trip to 3 s each.  asyncio.wait_for adds a hard outer limit of 5 s
      so a slow DNS lookup (e.g. unresolvable Docker hostname 'redis' when
      running lab 2 natively) can't stall process_job for 30-90 s.
    """
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=3,
        )
        key = _cache_key(query, symbols)
        cached = await asyncio.wait_for(r.get(key), timeout=5.0)
        await r.aclose()
        if cached:
            log.info("Cache hit — returning cached result", cache_key=key)
        return cached
    except Exception:
        return None


async def set_cached_result(query: str, symbols: List[str], result: str):
    """Store result in Redis with TTL.

    Teaching note (Lab 2):
      Same timeout guards as get_cached_result — fail fast and silently when
      Redis is unavailable (e.g. lab 2 running outside Docker).
    """
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=3,
        )
        key = _cache_key(query, symbols)
        await asyncio.wait_for(
            r.setex(key, settings.redis_cache_ttl_seconds, result),
            timeout=5.0,
        )
        await r.aclose()
        log.info("Result cached", cache_key=key, ttl=settings.redis_cache_ttl_seconds)
    except Exception:
        pass


async def update_job(job_id: str, updates: Dict[str, Any]):
    """Send a PATCH request to the job-api to update job status.

    Checks the response status explicitly: httpx doesn't raise on a non-2xx
    response on its own, so a rejected PATCH (wrong X-Internal-Token, job-api
    restarted and lost its in-memory job record, a 500 mid-request) would
    otherwise fail completely silently — this call returns normally, the
    worker logs "Job completed successfully", and the frontend is left
    polling a job stuck on RUNNING forever with nothing in any log
    explaining why. That combination (silent PATCH failure + Lab 2's
    in-memory job-api store, which is empty again after any job-api
    restart) is the most common cause of "the UI never shows the report."
    """
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.patch(
                f"{settings.job_api_url}/jobs/{job_id}",
                json=updates,
                headers={
                    "X-Internal-Token": settings.internal_api_token,
                    "X-Trace-ID": get_trace_id(),
                },
            )
            if resp.status_code >= 400:
                log.warning(
                    f"Job status update rejected by job-api: "
                    f"{resp.status_code} {resp.text[:300]}",
                    job_id=job_id,
                    job_api_url=settings.job_api_url,
                )
    except Exception as e:
        log.warning(f"Job status update failed: {e}", job_id=job_id)


async def process_job(message: PubSubMessage, allow_retry: bool = True) -> bool:
    """
    Process a single analysis job.

    Returns True if successful, False if should be retried.

    allow_retry=True (the default, used by the Pub/Sub path) means a
    failure below pubsub_max_retries is left as RUNNING rather than
    FAILED, on the assumption that Pub/Sub will redeliver the message and
    a later attempt will finish the job. analyze_sync() (Lab 2's direct
    HTTP mode) passes allow_retry=False: there is no redelivery in that
    mode — nothing will ever call this again for the same job — so the
    same logic would leave the job stuck on RUNNING forever with no error
    surfaced anywhere. See update_job()'s docstring for why that failure
    mode is easy to miss (no error in job-api's own logs, either).
    """
    set_correlation(trace_id=message.trace_id, job_id=message.job_id)
    log.info("Processing job", job_id=message.job_id, attempt=message.attempt_number)

    started_at = datetime.now(timezone.utc).isoformat()
    await update_job(message.job_id, {
        "status": "RUNNING",
        "started_at": started_at,
        "attempt_count": message.attempt_number,
    })

    request = message.request
    symbols = request.symbols or [s.strip() for s in request.query.upper().split() if len(s) >= 2 and s.isalpha()]
    cached = await get_cached_result(request.query, symbols)
    if cached:
        await update_job(message.job_id, {
            "status": "COMPLETED",
            "result": cached,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })
        log.info("Job completed from cache", job_id=message.job_id)
        return True

    guardrail = GuardrailPipeline(
        max_input_length=settings.guardrail_max_input_length,
        max_tool_calls=settings.guardrail_max_tool_calls,
        injection_detection=settings.guardrail_injection_detection,
    )
    allowed, guard_results = guardrail.check_input(request.query)
    guardrail_events = [
        {
            "layer": "INPUT",
            "check_name": r.check_name,
            "decision": r.decision,
            "reason": r.reason,
        }
        for r in guard_results
    ]

    if not allowed:
        blocked = [r for r in guard_results if not r.allowed]
        reason = blocked[0].reason if blocked else "Input rejected by guardrails"
        log.warning("Job blocked by input guardrails", job_id=message.job_id, reason=reason)
        await update_job(message.job_id, {
            "status": "FAILED",
            "last_error": f"Input guardrail blocked: {reason}",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "guardrail_events": guardrail_events,
        })
        return True

    primary_symbol = symbols[0] if symbols else "AAPL"

    mcp = MCPClient(settings.mcp_server_url)
    lf_trace = tracer.trace(
        name="crew-analysis",
        job_id=message.job_id,
        trace_id=message.trace_id,
        symbol=primary_symbol,
        query=request.query,
    )

    import re as _re
    def _strip_ansi(text: str) -> str:
        text = _re.sub(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])', '', text)
        return _re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)

    try:
        # The MCP session (real Streamable HTTP protocol against mcp-server's
        # /mcp sub-app) must stay open for the whole crew run: tools call it
        # lazily, only once an agent actually invokes them during kickoff().
        async with mcp:
            crew, hallucination_status = build_crew(
                primary_symbol, request.query, mcp, guardrail, lf_trace=lf_trace,
                provider=request.provider, api_key=message.api_key,
            )

            # Use langfuse.decorators.observe for job-level tracing.
            # langfuse.callback.CallbackHandler (LangChain-based) is broken with
            # langchain 1.x — the decorators API works without any langchain dependency.
            # This captures: input query, final output, total duration.
            # Per-LLM-call prompts/tokens require Langfuse server v3 + SDK v3 + OTel.
            # ToolTracer listens on CrewAI's own event bus, so it captures every
            # tool call regardless of transport (in-process, HTTP, or real MCP)
            # — it feeds the evidence-grounding check below.
            with ToolTracer() as grounding_tracer:
                # await asyncio.to_thread(...), not a direct call: crew.kickoff()
                # is a long blocking synchronous call, and process_job() runs on
                # the same event loop MCPClient captured as self._loop
                # (__aenter__). Call it directly here and that thread — the
                # loop's own thread — never returns to run_forever() until
                # kickoff() is done, so it can never pick up the
                # asyncio.run_coroutine_threadsafe(..., self._loop) calls that
                # CrewAI's tool-execution thread makes from inside kickoff().
                # Every MCP tool call then blocks for the full 30s client
                # timeout and fails — not because mcp-server is slow (its own
                # logs show sub-second responses), but because the coroutine
                # waiting to fetch that response is never scheduled. Running
                # kickoff() in its own thread leaves this thread's loop free to
                # actually service those calls.
                if _LANGFUSE_DECORATORS_OK and settings.langfuse_enabled:
                    @_langfuse_observe(name="crew-analysis")
                    def _kickoff():
                        _lf_ctx.update_current_trace(
                            session_id=message.job_id,
                            user_id=request.user_id,
                            metadata={"symbol": primary_symbol, "query": request.query},
                        )
                        return crew.kickoff()
                    result = await asyncio.to_thread(_kickoff)
                else:
                    result = await asyncio.to_thread(crew.kickoff)

        result_text = _strip_ansi(str(result))

        safe_output, output_events = guardrail.check_output(
            result_text, evidence=grounding_tracer.evidence
        )
        guardrail_events += [
            {
                "layer": "OUTPUT",
                "check_name": r.check_name,
                "decision": r.decision,
                "reason": r.reason,
            }
            for r in output_events
        ]
        # Informational only, like grounding: reports whether CrewAI's own
        # built-in guardrail (attached to the report task in build_crew) is
        # actually enforcing anything in this crewai install, or just
        # logging a no-op — see _hallucination_guardrail()'s docstring.
        guardrail_events.append({
            "layer": "OUTPUT",
            "check_name": "crewai_hallucination_guardrail",
            "decision": "ALLOW",
            "reason": hallucination_status.get("note", ""),
        })

        await set_cached_result(request.query, symbols, safe_output)

        tool_trace = [t.model_dump(mode="json") for t in mcp.tool_trace]

        await update_job(message.job_id, {
            "status": "COMPLETED",
            "result": safe_output,
            "tool_trace": tool_trace,
            "guardrail_events": guardrail_events,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })

        tracer.flush()
        log.info(
            "Job completed successfully",
            job_id=message.job_id,
            tool_calls=len(tool_trace),
        )
        return True

    except Exception as e:
        log.error(f"Job processing failed: {e}", job_id=message.job_id)
        tracer.flush()

        if not allow_retry or message.attempt_number >= settings.pubsub_max_retries:
            await update_job(message.job_id, {
                "status": "FAILED",
                "last_error": str(e),
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "guardrail_events": guardrail_events,
            })
            return True

        return False


async def run_pubsub_worker():
    """
    Pull messages from Pub/Sub and process them.

    Teaching note:
      This is the async worker loop.
      In GKE, multiple pods run this same loop concurrently.
      Pub/Sub guarantees at-least-once delivery — our idempotency check
      (checking Firestore for COMPLETED status) prevents double-processing.
    """
    if settings.use_pubsub_emulator:
        os.environ["PUBSUB_EMULATOR_HOST"] = settings.pubsub_emulator_host

    try:
        from google.cloud import pubsub_v1
    except ImportError:
        log.error("google-cloud-pubsub not installed")
        return

    subscriber = pubsub_v1.SubscriberClient()
    subscription_path = subscriber.subscription_path(
        settings.pubsub_project_id,
        settings.pubsub_subscription,
    )
    log.info(f"Listening on {subscription_path}")

    def callback(pubsub_message):
        """
        Pub/Sub calls this in a ThreadPoolExecutor thread — there is NO
        existing event loop in that thread. We must create one per message.
        """
        try:
            data = json.loads(pubsub_message.data.decode("utf-8"))
            message = PubSubMessage.model_validate(data)
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                success = loop.run_until_complete(process_job(message))
            finally:
                loop.close()
            if success:
                pubsub_message.ack()
                log.info("Message acknowledged", job_id=message.job_id)
            else:
                pubsub_message.nack()
                log.warning("Message nacked for retry", job_id=message.job_id)
        except Exception as e:
            log.error(f"Message processing error: {e}")
            pubsub_message.nack()

    streaming_pull_future = subscriber.subscribe(subscription_path, callback=callback)
    log.info("Worker started — waiting for messages")

    try:
        streaming_pull_future.result()
    except KeyboardInterrupt:
        streaming_pull_future.cancel()
        log.info("Worker stopped")


from fastapi import FastAPI as _FastAPI

http_app = _FastAPI(title="Agent Runtime", version="1.0.0")


@http_app.get("/health")
async def health():
    return {"status": "ok", "service": "agent-runtime", "mode": "http"}


@http_app.post("/analyze")
async def analyze_sync(body: Dict[str, Any]):
    """
    Synchronous analysis endpoint for local development.

    Teaching note:
      This is Stage 1 / Stage 2 mode — direct HTTP call, blocking.
      The frontend waits for this to complete.
      Use this for local dev; use Pub/Sub mode for production.

      The job-api sends a full PubSubMessage envelope (contains job_id,
      request, trace_id). We detect this and parse accordingly so that
      job_id and trace_id are preserved end-to-end (visible in logs).
    """
    from models import AnalysisRequest

    if "request" in body and "job_id" in body:
        message = PubSubMessage.model_validate(body)
    else:
        # A flat request (no PubSubMessage envelope) is how you'd call this
        # directly while testing — e.g. curl -d '{"query": "...",
        # "provider": "openai", "api_key": "sk-..."}'. api_key isn't a field
        # on AnalysisRequest (it must not end up in a persisted JobRecord —
        # see the note on PubSubMessage.api_key), so it's pulled off here.
        body = dict(body)
        flat_api_key = body.pop("api_key", "")
        request = AnalysisRequest(**body)
        message = PubSubMessage(
            job_id=str(uuid.uuid4()),
            request=request,
            trace_id=str(uuid.uuid4()),
            api_key=flat_api_key,
        )
    # allow_retry=False: this is a direct HTTP call with no Pub/Sub behind
    # it, so nothing will ever retry a failed attempt — a failure must be
    # marked FAILED immediately or the job is stuck on RUNNING forever.
    success = await process_job(message, allow_retry=False)
    return {"job_id": message.job_id, "success": success}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=["pubsub", "http"],
        default=os.getenv("WORKER_MODE", "pubsub"),
        help="pubsub: consume from Pub/Sub queue (production), http: serve HTTP API (dev)",
    )
    args = parser.parse_args()

    if args.mode == "pubsub":
        asyncio.run(run_pubsub_worker())
    else:
        import uvicorn
        port = int(os.getenv("PORT", "8002"))
        uvicorn.run(http_app, host="0.0.0.0", port=port)
