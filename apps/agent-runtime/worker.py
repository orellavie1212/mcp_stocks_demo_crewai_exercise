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
import base64
import hashlib
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

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


class MCPClient:
    """
    HTTP client for the MCP server.

    Teaching note:
      In the demo, agents.py did `from mcp_server import get_tools_by_names`.
      That's a direct Python import — tightly coupled.

      Now, the agent-runtime calls the MCP server via HTTP.
      Benefits:
      - MCP server can be deployed/updated independently
      - Different languages could implement the MCP server
      - Tool calls are visible in network logs (debuggable)
      - MCP server can be rate-limited independently
    """

    def __init__(self, base_url: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._tool_trace: List[ToolCallRecord] = []

    def _make_headers(self) -> Dict[str, str]:
        """Pass correlation IDs to the MCP server for end-to-end tracing."""
        return {
            "X-Trace-ID": get_trace_id(),
            "X-Job-ID": get_job_id(),
            "X-Internal-Token": settings.internal_api_token,
            "Content-Type": "application/json",
        }

    async def call(self, tool: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Call an MCP tool endpoint and return the parsed JSON response.
        Records tool call in the trace for the job record.
        """
        start = time.perf_counter()
        record = ToolCallRecord(
            tool_name=tool,
            arguments=payload,
        )

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(
                    f"{self.base_url}/{tool}",
                    json=payload,
                    headers=self._make_headers(),
                )
                response.raise_for_status()
                result = response.json()
                duration_ms = (time.perf_counter() - start) * 1000

                record.success = True
                record.duration_ms = round(duration_ms, 1)
                record.result_preview = str(result)[:300]
                self._tool_trace.append(record)

                log.info(
                    f"MCP tool call succeeded",
                    tool_name=tool,
                    duration_ms=record.duration_ms,
                )
                return result

            except Exception as e:
                duration_ms = (time.perf_counter() - start) * 1000
                record.success = False
                record.error = str(e)
                record.duration_ms = round(duration_ms, 1)
                self._tool_trace.append(record)

                log.error(
                    f"MCP tool call failed: {e}",
                    tool_name=tool,
                    duration_ms=duration_ms,
                )
                return {"error": str(e)}

    def call_sync(self, tool: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Synchronous HTTP call for use inside CrewAI tool callbacks.

        Teaching note:
          CrewAI 1.x runs tool functions as plain sync callables inside a
          ThreadPoolExecutor. The outer process_job() runs in an async event
          loop, so asyncio.run() inside a tool raises:
            'asyncio.run() cannot be called from a running event loop'
          Solution: use httpx.Client (blocking) for the tool layer.
          The outer async infrastructure (Redis, job-api PATCH, etc.) is
          unaffected — only the per-tool MCP call uses sync I/O here.
        """
        start = time.perf_counter()
        record = ToolCallRecord(tool_name=tool, arguments=payload)

        with httpx.Client(timeout=self.timeout) as client:
            try:
                response = client.post(
                    f"{self.base_url}/{tool}",
                    json=payload,
                    headers=self._make_headers(),
                )
                response.raise_for_status()
                result = response.json()
                duration_ms = (time.perf_counter() - start) * 1000

                record.success = True
                record.duration_ms = round(duration_ms, 1)
                record.result_preview = str(result)[:300]
                self._tool_trace.append(record)

                log.info("MCP tool call succeeded", tool_name=tool, duration_ms=record.duration_ms)
                return result

            except Exception as e:
                duration_ms = (time.perf_counter() - start) * 1000
                record.success = False
                record.error = str(e)
                record.duration_ms = round(duration_ms, 1)
                self._tool_trace.append(record)

                log.error(f"MCP tool call failed: {e}", tool_name=tool, duration_ms=duration_ms)
                return {"error": str(e)}

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
        result = mcp.call_sync("search", {"q": q})
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
        r = mcp.call_sync("quote", {"symbol": symbol.upper()})
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
        result = mcp.call_sync("series", {"symbol": symbol.upper(), "lookback": 180})
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
        r = mcp.call_sync("events", {"symbol": symbol.upper()})
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


def build_crew(symbol: str, query: str, mcp: MCPClient, guardrail: GuardrailPipeline, lf_trace=None) -> Crew:
    """
    Build the 4-agent CrewAI crew.

    Teaching note:
      Same 4 agents as the demo, but now:
      - They use Gemini instead of OpenAI
      - Their tools call the MCP server over HTTP (not direct import)
      - The Langfuse callback auto-traces every LLM call
      - Model routing: all agents use MAIN tier (gemini-2.5-flash)
        except the report writer uses STRONG tier (gemini-2.5-pro)

      Pass lf_trace so the CrewAI LLM spans are nested under the
      same job trace in Langfuse (visible as a single trace tree).
    """
    tools = make_crewai_tools(mcp, guardrail)
    research_tools = tools[:3]
    technical_tools = tools[3:]
    sector_tools = tools[:2] + [tools[3]]

    main_llm = get_llm("main")
    strong_llm = get_llm("strong")

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
    )

    return Crew(
        agents=[research_agent, technical_agent, sector_agent, report_agent],
        tasks=[research_task, technical_task, sector_task, report_task],
        process=Process.sequential,
        verbose=True,
        callbacks=callbacks,
    )


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
    """Send a PATCH request to the job-api to update job status."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.patch(
                f"{settings.job_api_url}/jobs/{job_id}",
                json=updates,
                headers={
                    "X-Internal-Token": settings.internal_api_token,
                    "X-Trace-ID": get_trace_id(),
                },
            )
    except Exception as e:
        log.warning(f"Job status update failed: {e}", job_id=job_id)


async def process_job(message: PubSubMessage) -> bool:
    """
    Process a single analysis job.

    Returns True if successful, False if should be retried.
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
        crew = build_crew(primary_symbol, request.query, mcp, guardrail, lf_trace=lf_trace)

        # Use langfuse.decorators.observe for job-level tracing.
        # langfuse.callback.CallbackHandler (LangChain-based) is broken with
        # langchain 1.x — the decorators API works without any langchain dependency.
        # This captures: input query, final output, total duration.
        # Per-LLM-call prompts/tokens require Langfuse server v3 + SDK v3 + OTel.
        if _LANGFUSE_DECORATORS_OK and settings.langfuse_enabled:
            @_langfuse_observe(name="crew-analysis")
            def _kickoff():
                _lf_ctx.update_current_trace(
                    session_id=message.job_id,
                    user_id=request.user_id,
                    metadata={"symbol": primary_symbol, "query": request.query},
                )
                return crew.kickoff()
            result = _kickoff()
        else:
            result = crew.kickoff()

        result_text = _strip_ansi(str(result))

        safe_output, output_events = guardrail.check_output(result_text)
        guardrail_events += [
            {
                "layer": "OUTPUT",
                "check_name": r.check_name,
                "decision": r.decision,
                "reason": r.reason,
            }
            for r in output_events
        ]

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

        if message.attempt_number >= settings.pubsub_max_retries:
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
        request = AnalysisRequest(**body)
        message = PubSubMessage(
            job_id=str(uuid.uuid4()),
            request=request,
            trace_id=str(uuid.uuid4()),
        )
    success = await process_job(message)
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
