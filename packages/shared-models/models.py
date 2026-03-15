"""
shared-models — Pydantic data models shared across all services.

Teaching note:
  These models are the "contract" between services.
  Any change here is a breaking change that affects all services — just like
  an API schema change in a real microservices architecture.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


class JobStatus(str, Enum):
    """Lifecycle of an analysis job."""
    PENDING   = "PENDING"
    RUNNING   = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED    = "FAILED"
    CANCELLED = "CANCELLED"


class GuardrailLayer(str, Enum):
    """Which layer triggered a guardrail decision."""
    INPUT  = "INPUT"
    TOOL   = "TOOL"
    OUTPUT = "OUTPUT"


class GuardrailDecision(str, Enum):
    ALLOW  = "ALLOW"
    BLOCK  = "BLOCK"
    MODIFY = "MODIFY"


class ModelTier(str, Enum):
    """Which LLM tier to use — controls cost routing."""
    FAST   = "fast"
    MAIN   = "main"
    STRONG = "strong"


class AnalysisRequest(BaseModel):
    """
    A user's stock analysis request.

    Submitted by the frontend, validated by the job-api,
    published to Pub/Sub for the agent-runtime to consume.
    """
    query: str = Field(
        ...,
        min_length=3,
        max_length=2000,
        description="Natural language question about one or more stocks"
    )
    symbols: List[str] = Field(
        default_factory=list,
        description="Explicit stock tickers (optional — agent can infer from query)"
    )
    user_id: str = Field(
        default="anonymous",
        description="User identifier for rate limiting and audit"
    )
    idempotency_key: Optional[str] = Field(
        default=None,
        description="Client-provided dedup key. If provided, duplicate submissions return the same job."
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary key/value pairs for extensibility"
    )

    @field_validator("symbols", mode="before")
    @classmethod
    def uppercase_symbols(cls, v):
        if isinstance(v, list):
            return [s.upper().strip() for s in v if s]
        return v


class JobRecord(BaseModel):
    """
    Persisted in Firestore. Tracks the full lifecycle of an analysis job.

    The frontend polls GET /jobs/{job_id} which reads this document.
    Firestore real-time listeners can push updates without polling.
    """
    job_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique job identifier (UUID)"
    )
    request: AnalysisRequest
    status: JobStatus = JobStatus.PENDING

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    attempt_count: int = 0
    max_attempts: int = 3
    last_error: Optional[str] = None

    result: Optional[str] = None
    tool_trace: List["ToolCallRecord"] = Field(default_factory=list)
    usage: Optional["UsageRecord"] = None

    guardrail_events: List["GuardrailEvent"] = Field(default_factory=list)

    def to_firestore(self) -> Dict[str, Any]:
        """Serialize for Firestore (converts datetimes to ISO strings)."""
        d = self.model_dump(mode="json")
        return d

    @classmethod
    def from_firestore(cls, data: Dict[str, Any]) -> "JobRecord":
        """Deserialize from Firestore document."""
        return cls.model_validate(data)


class JobStatusResponse(BaseModel):
    """
    Returned by GET /jobs/{job_id}.
    A lightweight view over JobRecord for the frontend.
    """
    job_id: str
    status: JobStatus
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    latency_seconds: Optional[float] = None
    attempt_count: int = 0
    request: Optional["AnalysisRequest"] = None
    result: Optional[str] = None
    tool_trace: List["ToolCallRecord"] = Field(default_factory=list)
    usage: Optional["UsageRecord"] = None
    guardrail_events: List["GuardrailEvent"] = Field(default_factory=list)
    error: Optional[str] = None

    @classmethod
    def from_job_record(cls, job: JobRecord) -> "JobStatusResponse":
        latency = None
        if job.completed_at and job.started_at:
            latency = (job.completed_at - job.started_at).total_seconds()
        return cls(
            job_id=job.job_id,
            status=job.status,
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
            latency_seconds=latency,
            attempt_count=job.attempt_count,
            request=job.request,
            result=job.result,
            tool_trace=job.tool_trace,
            usage=job.usage,
            guardrail_events=job.guardrail_events,
            error=job.last_error,
        )


class ToolCallRecord(BaseModel):
    """
    Records a single MCP tool invocation.

    Stored in the JobRecord and displayed in the frontend's "tool trace" panel.
    In production, also sent to Langfuse as a span.
    """
    tool_name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    success: bool = True
    result_preview: str = ""
    error: Optional[str] = None
    duration_ms: float = 0.0
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    langfuse_span_id: Optional[str] = None


class UsageRecord(BaseModel):
    """
    Aggregated LLM usage for a complete job.

    Teaching note:
      This is how you implement cost governance.
      Every job has an associated cost. You can set per-user budgets
      and alert when costs spike (e.g., a student's malformed query
      triggers infinite retries at $0.50/attempt).
    """
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0

    model_usage: Dict[str, "ModelUsage"] = Field(default_factory=dict)

    def add(self, model: str, input_tokens: int, output_tokens: int, cost: float):
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        self.total_tokens += input_tokens + output_tokens
        self.estimated_cost_usd += cost
        if model not in self.model_usage:
            self.model_usage[model] = ModelUsage(model=model)
        self.model_usage[model].input_tokens += input_tokens
        self.model_usage[model].output_tokens += output_tokens
        self.model_usage[model].cost_usd += cost


class ModelUsage(BaseModel):
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class GuardrailEvent(BaseModel):
    """
    Records a guardrail check decision.

    Teaching note:
      These events are returned to the frontend and logged with the job,
      so students can see exactly where and why content was blocked/modified.
      This makes the system transparent and debuggable — essential for teaching.
    """
    layer: GuardrailLayer
    check_name: str
    decision: GuardrailDecision
    reason: str = ""
    original_value: Optional[str] = None
    modified_value: Optional[str] = None
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class PubSubMessage(BaseModel):
    """
    Message published to Pub/Sub analysis-requests topic.

    Teaching note:
      This envelope pattern (message + metadata) is standard.
      The job_id acts as the idempotency key — if the worker processes
      the same message twice (exactly-once is not guaranteed), it checks
      Firestore for COMPLETED status and skips reprocessing.
    """
    job_id: str
    request: AnalysisRequest
    attempt_number: int = 1
    trace_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Distributed trace ID — correlates logs across all services"
    )
    published_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class StockQuote(BaseModel):
    symbol: str
    price: Optional[float] = None
    change: Optional[float] = None
    change_percent: Optional[float] = None
    volume: Optional[float] = None
    timestamp: Optional[int] = None
    error: Optional[str] = None


class TechnicalIndicators(BaseModel):
    symbol: str
    last_close: Optional[float] = None
    sma: Optional[float] = None
    ema: Optional[float] = None
    rsi: Optional[float] = None
    error: Optional[str] = None


class MarketEvents(BaseModel):
    symbol: str
    date: Optional[str] = None
    gap_up: bool = False
    gap_down: bool = False
    vol_spike: bool = False
    is_52w_high: bool = False
    is_52w_low: bool = False
    error: Optional[str] = None


JobRecord.model_rebuild()
JobStatusResponse.model_rebuild()
UsageRecord.model_rebuild()
