"""
shared-observability — Structured logging, OpenTelemetry tracing, and Langfuse integration.

Teaching note:
  Observability is NOT optional in production AI systems.
  Without it, you have no idea:
  - Why a request failed
  - How much it cost
  - Which agent step took 45 seconds
  - Whether the guardrails are firing too often

  This module wires three tools together:
  1. Structured JSON logging  → Cloud Logging (searchable, filterable)
  2. OpenTelemetry tracing    → Cloud Trace (distributed request traces)
  3. Langfuse                 → LLM call traces (prompts, tokens, costs)
"""
from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Dict, Generator, Optional

# =============================================================================
# Structured JSON Logger
# =============================================================================

class StructuredFormatter(logging.Formatter):
    """
    Formats log records as single-line JSON objects.

    Every log line has:
      - timestamp: ISO 8601
      - level: INFO / WARNING / ERROR
      - service: which container emitted this
      - trace_id: correlates logs across all services for one request
      - job_id: correlates logs across the job lifecycle
      - message: human-readable text
      - plus any extra fields passed via extra={...}

    In Cloud Logging, you can filter:
      jsonPayload.trace_id="abc-123"
    to see the full journey of one request.
    """

    def format(self, record: logging.LogRecord) -> str:
        log_object: Dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "service": getattr(record, "service", "unknown"),
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Correlation IDs
        if trace_id := getattr(record, "trace_id", None):
            log_object["trace_id"] = trace_id
        if job_id := getattr(record, "job_id", None):
            log_object["job_id"] = job_id

        # Performance fields
        if duration_ms := getattr(record, "duration_ms", None):
            log_object["duration_ms"] = duration_ms

        # Cost / token fields
        if tokens := getattr(record, "tokens_used", None):
            log_object["tokens_used"] = tokens
        if cost := getattr(record, "cost_usd", None):
            log_object["cost_usd"] = cost

        # Tool tracing fields
        if tool_name := getattr(record, "tool_name", None):
            log_object["tool_name"] = tool_name
        if agent_name := getattr(record, "agent_name", None):
            log_object["agent_name"] = agent_name

        # Exception info
        if record.exc_info:
            log_object["exception"] = self.formatException(record.exc_info)

        # Any extra fields
        for key, value in record.__dict__.items():
            if key not in {
                "name", "msg", "args", "levelname", "levelno", "pathname",
                "filename", "module", "exc_info", "exc_text", "stack_info",
                "lineno", "funcName", "created", "msecs", "relativeCreated",
                "thread", "threadName", "processName", "process", "message",
                "taskName",
            } and not key.startswith("_"):
                if key not in log_object:
                    log_object[key] = value

        return json.dumps(log_object, default=str)


class TextFormatter(logging.Formatter):
    """Human-readable formatter for local development."""

    COLORS = {
        "DEBUG": "\033[36m",    # Cyan
        "INFO": "\033[32m",     # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",    # Red
        "CRITICAL": "\033[35m", # Magenta
        "RESET": "\033[0m",
    }

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, self.COLORS["RESET"])
        reset = self.COLORS["RESET"]
        service = getattr(record, "service", "")
        trace_id = getattr(record, "trace_id", "")
        trace_short = f"[{trace_id[:8]}]" if trace_id else ""
        duration = getattr(record, "duration_ms", None)
        dur_str = f" ({duration:.0f}ms)" if duration else ""

        return (
            f"{color}[{record.levelname}]{reset} "
            f"{service}{trace_short} "
            f"{record.getMessage()}{dur_str}"
        )


def setup_logging(
    service_name: str,
    log_level: str = "INFO",
    log_format: str = "json",
) -> logging.Logger:
    """
    Configure and return the root logger for a service.

    Call once at service startup:
      logger = setup_logging("mcp-server", log_level="INFO", log_format="json")
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Remove existing handlers
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)

    if log_format == "json":
        handler.setFormatter(StructuredFormatter())
    else:
        handler.setFormatter(TextFormatter())

    root.addHandler(handler)

    # Silence noisy libraries
    for lib in ("urllib3", "httpx", "httpcore", "google.auth", "grpc"):
        logging.getLogger(lib).setLevel(logging.WARNING)

    logger = logging.getLogger(service_name)

    class ServiceAdapter(logging.LoggerAdapter):
        def process(self, msg, kwargs):
            kwargs.setdefault("extra", {})["service"] = service_name
            return msg, kwargs

    return ServiceAdapter(logger, {})


# =============================================================================
# Correlation context (trace_id, job_id)
# =============================================================================

# Thread-local / async-local correlation IDs
import contextvars

_trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "trace_id", default=""
)
_job_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "job_id", default=""
)


def set_correlation(trace_id: str = "", job_id: str = ""):
    """Set correlation IDs for the current async context."""
    if trace_id:
        _trace_id_var.set(trace_id)
    if job_id:
        _job_id_var.set(job_id)


def get_trace_id() -> str:
    return _trace_id_var.get() or str(uuid.uuid4())


def get_job_id() -> str:
    return _job_id_var.get()


class CorrelatedLogger:
    """
    Logger wrapper that automatically injects trace_id and job_id into every log.

    Usage:
        log = CorrelatedLogger("agent-runtime")
        log.info("Starting crew", tool_name="research")
    """

    def __init__(self, service: str, base_logger: Optional[logging.Logger] = None):
        self.service = service
        self._logger = base_logger or logging.getLogger(service)

    def _extra(self, **kwargs) -> Dict[str, Any]:
        extra = {
            "service": self.service,
            "trace_id": get_trace_id(),
            "job_id": get_job_id(),
        }
        extra.update(kwargs)
        return extra

    def info(self, msg: str, **kwargs):
        self._logger.info(msg, extra=self._extra(**kwargs))

    def warning(self, msg: str, **kwargs):
        self._logger.warning(msg, extra=self._extra(**kwargs))

    def error(self, msg: str, **kwargs):
        self._logger.error(msg, extra=self._extra(**kwargs))

    def debug(self, msg: str, **kwargs):
        self._logger.debug(msg, extra=self._extra(**kwargs))

    def exception(self, msg: str, **kwargs):
        self._logger.exception(msg, extra=self._extra(**kwargs))


# =============================================================================
# Timing utilities
# =============================================================================

@contextmanager
def timed(logger: CorrelatedLogger, operation: str, **extra) -> Generator:
    """
    Context manager that logs duration of a block.

    Usage:
        with timed(log, "mcp_tool_call", tool_name="get_quote"):
            result = call_mcp_tool(...)
    """
    start = time.perf_counter()
    try:
        yield
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(f"{operation} completed", duration_ms=duration_ms, **extra)
    except Exception as e:
        duration_ms = (time.perf_counter() - start) * 1000
        logger.error(
            f"{operation} failed: {e}",
            duration_ms=duration_ms,
            **extra
        )
        raise


def timed_fn(operation: str = "", **extra_kwargs):
    """Decorator version of timed()."""
    def decorator(fn):
        @wraps(fn)
        async def async_wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                result = await fn(*args, **kwargs)
                duration_ms = (time.perf_counter() - start) * 1000
                logging.getLogger(fn.__module__).info(
                    f"{operation or fn.__name__} completed",
                    extra={"duration_ms": duration_ms, **extra_kwargs}
                )
                return result
            except Exception as e:
                duration_ms = (time.perf_counter() - start) * 1000
                logging.getLogger(fn.__module__).error(
                    f"{operation or fn.__name__} failed: {e}",
                    extra={"duration_ms": duration_ms, **extra_kwargs}
                )
                raise
        @wraps(fn)
        def sync_wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                result = fn(*args, **kwargs)
                duration_ms = (time.perf_counter() - start) * 1000
                logging.getLogger(fn.__module__).info(
                    f"{operation or fn.__name__} completed",
                    extra={"duration_ms": duration_ms, **extra_kwargs}
                )
                return result
            except Exception as e:
                duration_ms = (time.perf_counter() - start) * 1000
                logging.getLogger(fn.__module__).error(
                    f"{operation or fn.__name__} failed: {e}",
                    extra={"duration_ms": duration_ms, **extra_kwargs}
                )
                raise

        import asyncio
        if asyncio.iscoroutinefunction(fn):
            return async_wrapper
        return sync_wrapper
    return decorator


# =============================================================================
# Langfuse integration (LLM call tracing)
# =============================================================================

class LangfuseTracer:
    """
    Wraps Langfuse to trace LLM calls with correlation to job IDs.

    Teaching note:
      Langfuse shows you the FULL conversation:
      - What prompt was sent to Gemini
      - What Gemini responded
      - How many tokens it used
      - What it cost
      - How long it took
      - The entire chain: Frontend → Job API → Agent → Tool → Gemini

      This is invaluable for debugging "why did the agent say X?"
      and for cost attribution.
    """

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self._client = None

        if enabled:
            try:
                from langfuse import Langfuse
                # Langfuse reads LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY,
                # and LANGFUSE_HOST from environment automatically
                self._client = Langfuse()
            except ImportError:
                logging.warning(
                    "langfuse not installed — LLM tracing disabled. "
                    "Run: pip install langfuse"
                )
                self.enabled = False
            except Exception as e:
                logging.warning(f"Langfuse init failed: {e} — tracing disabled")
                self.enabled = False

    def trace(self, name: str, job_id: str = "", trace_id: str = "", **metadata):
        """Start a Langfuse trace for a job."""
        if not self.enabled or not self._client:
            return _NullTrace()
        try:
            return self._client.trace(
                name=name,
                id=trace_id or get_trace_id(),
                session_id=job_id or get_job_id(),
                metadata=metadata,
            )
        except Exception as e:
            logging.warning(f"Langfuse trace failed: {e}")
            return _NullTrace()

    def get_callback(self):
        """
        Returns a LangfuseCallbackHandler for CrewAI / LangChain.

        Plug this into CrewAI to auto-trace ALL LLM calls:
            crew = Crew(..., callbacks=[tracer.get_callback()])

        Teaching note:
          This callback intercepts every LLM call CrewAI makes and sends
          the full prompt + response + token count to Langfuse.
          You can then see in Langfuse UI:
          - What prompt was sent to Gemini
          - What Gemini responded
          - How many tokens it used (and estimated cost)
          - Which agent step triggered this call
        """
        if not self.enabled or not self._client:
            return None
        try:
            from langfuse.callback import CallbackHandler
            return CallbackHandler(
                public_key=None,   # reads from env
                secret_key=None,   # reads from env
                host=None,         # reads from env
                session_id=get_job_id(),
                trace_name="crew-run",
            )
        except Exception as e:
            logging.warning(f"Langfuse callback init failed: {e}")
            return None

    def flush(self):
        """Flush pending traces (call at job completion)."""
        if self.enabled and self._client:
            try:
                self._client.flush()
            except Exception:
                pass


class _NullTrace:
    """No-op trace when Langfuse is disabled — avoids None checks everywhere."""

    def span(self, *args, **kwargs):
        return self

    def generation(self, *args, **kwargs):
        return self

    def score(self, *args, **kwargs):
        return self

    def update(self, *args, **kwargs):
        return self

    def end(self, *args, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


# =============================================================================
# OpenTelemetry (Cloud Trace)
# =============================================================================

def setup_tracing(service_name: str, gcp_project: str = ""):
    """
    Configure OpenTelemetry to export traces to GCP Cloud Trace.

    Teaching note:
      Cloud Trace shows the full distributed timeline:
      frontend → job-api → Pub/Sub → agent-runtime → MCP server → Gemini

      You can see exactly where the 45-second slowdown is hiding.
      This runs automatically — no code changes needed in handlers.
    """
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.resources import Resource

        resource = Resource.create({
            "service.name": service_name,
            "service.version": "1.0.0",
        })
        provider = TracerProvider(resource=resource)

        if gcp_project:
            # Export to Cloud Trace (production)
            try:
                from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
                exporter = CloudTraceSpanExporter(project_id=gcp_project)
                provider.add_span_processor(BatchSpanProcessor(exporter))
            except ImportError:
                pass  # opentelemetry-exporter-gcp-trace not installed
        else:
            # Local: print to console
            from opentelemetry.sdk.trace.export import ConsoleSpanExporter
            provider.add_span_processor(
                BatchSpanProcessor(ConsoleSpanExporter())
            )

        trace.set_tracer_provider(provider)
    except ImportError:
        pass  # OpenTelemetry not installed — tracing silently disabled


# =============================================================================
# Gemini cost estimation (approximate)
# =============================================================================

# Approximate prices per 1M tokens (USD) as of early 2026
# These are estimates — check https://cloud.google.com/vertex-ai/pricing for current rates
GEMINI_PRICING = {
    "gemini-2.5-flash-lite": {"input": 0.075,  "output": 0.30},
    "gemini-2.5-flash":      {"input": 0.075,  "output": 0.30},
    "gemini-2.5-pro":        {"input": 1.25,   "output": 5.00},
    # Fallback for unknown models
    "default":               {"input": 0.075,  "output": 0.30},
}


def estimate_cost(
    model: str, input_tokens: int, output_tokens: int
) -> float:
    """Return estimated cost in USD for an LLM call."""
    pricing = GEMINI_PRICING.get(model, GEMINI_PRICING["default"])
    input_cost = (input_tokens / 1_000_000) * pricing["input"]
    output_cost = (output_tokens / 1_000_000) * pricing["output"]
    return round(input_cost + output_cost, 6)
