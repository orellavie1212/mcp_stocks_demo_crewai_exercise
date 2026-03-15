"""
Job API — The async gateway service.

Teaching note:
  This is the thin layer between the user-facing frontend and the
  heavy agent-runtime workers.

  Pattern: "Accept, Persist, Publish, Return"
  1. ACCEPT  — Validate the request with Pydantic
  2. PERSIST — Write a PENDING job record to Firestore
  3. PUBLISH — Put a message on Pub/Sub
  4. RETURN  — Return {job_id} to the frontend immediately (no waiting!)

  The frontend then polls GET /jobs/{job_id} until status=COMPLETED.
  This is the async pattern that lets us handle 100 concurrent requests
  with a single frontend pod.

  Compare with demo:
    Demo: streamlit blocks for 2 minutes while crew runs
    Production: frontend submits, returns in <100ms, polls for result
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

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

from config import get_settings
from observability import setup_logging, CorrelatedLogger, set_correlation, get_trace_id
from models import (
    AnalysisRequest, JobRecord, JobStatus, JobStatusResponse, PubSubMessage
)
from guardrails import InputGuardrails

settings = get_settings()
setup_logging("job-api", settings.log_level, settings.log_format)
log = CorrelatedLogger("job-api")

app = FastAPI(
    title="Stock Agent Job API",
    version="1.0.0",
    description=(
        "Async job gateway. Accepts analysis requests, stores them in Firestore, "
        "publishes to Pub/Sub for async processing by agent workers."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_firestore():
    """
    Return a Firestore client.

    Teaching note:
      In production (GKE), this uses Workload Identity — no credentials needed.
      Locally, it uses the GOOGLE_APPLICATION_CREDENTIALS env var or the
      Firestore emulator (FIRESTORE_EMULATOR_HOST).
    """
    try:
        from google.cloud import firestore
        if settings.use_firestore_emulator:
            os.environ["FIRESTORE_EMULATOR_HOST"] = settings.firestore_emulator_host
        return firestore.AsyncClient(project=settings.firestore_project_id)
    except ImportError:
        log.warning("google-cloud-firestore not installed — using in-memory fallback")
        return None
    except Exception as e:
        log.warning(f"Firestore unavailable ({type(e).__name__}) — using in-memory fallback")
        return None


_IN_MEMORY_JOBS: Dict[str, Dict] = {}


async def save_job(job: JobRecord):
    """Persist job to Firestore (or in-memory fallback)."""
    data = job.to_firestore()
    db = get_firestore()
    if db:
        try:
            doc_ref = db.collection("jobs").document(job.job_id)
            await doc_ref.set(data)
            log.info("Job saved to Firestore", job_id=job.job_id)
            return
        except Exception as e:
            log.warning(f"Firestore write failed: {e} — falling back to in-memory")
    _IN_MEMORY_JOBS[job.job_id] = data
    log.info("Job saved in-memory", job_id=job.job_id)


async def load_job(job_id: str) -> Optional[JobRecord]:
    """Load job from Firestore (or in-memory fallback)."""
    db = get_firestore()
    if db:
        try:
            doc = await db.collection("jobs").document(job_id).get()
            if doc.exists:
                return JobRecord.from_firestore(doc.to_dict())
        except Exception as e:
            log.warning(f"Firestore read failed: {e} — checking in-memory")

    if job_id in _IN_MEMORY_JOBS:
        return JobRecord.from_firestore(_IN_MEMORY_JOBS[job_id])
    return None


async def update_job_status(job_id: str, updates: Dict[str, Any]):
    """Partially update a job record."""
    db = get_firestore()
    if db:
        try:
            await db.collection("jobs").document(job_id).update(updates)
            return
        except Exception as e:
            log.warning(f"Firestore update failed: {e}")
    if job_id in _IN_MEMORY_JOBS:
        _IN_MEMORY_JOBS[job_id].update(updates)


def get_pubsub_publisher():
    """
    Return a Pub/Sub PublisherClient.

    Teaching note:
      When PUBSUB_EMULATOR_HOST is set, Google's SDK automatically
      routes to the local emulator — zero code change needed.
      This is the same pattern as Firestore emulator.
    """
    try:
        from google.cloud import pubsub_v1
        if settings.use_pubsub_emulator:
            os.environ["PUBSUB_EMULATOR_HOST"] = settings.pubsub_emulator_host
        return pubsub_v1.PublisherClient()
    except ImportError:
        log.warning("google-cloud-pubsub not installed — using in-memory queue")
        return None


_IN_MEMORY_QUEUE = []


async def _call_agent_runtime_http(message: PubSubMessage):
    """
    Fire-and-forget background task: call agent-runtime /analyze over HTTP.

    Teaching note (Lab 2):
      This is the Lab 2 pattern — no Pub/Sub, but the job is still async
      from the user's perspective (202 → poll).  The agent-runtime runs in
      --mode http and exposes POST /analyze.  Once it finishes it PATCHes
      back to job-api to update job status, exactly like the Pub/Sub path.

      Difference from Lab 3 (Pub/Sub):
      - Lab 2: job-api calls agent-runtime directly (tight HTTP coupling)
      - Lab 3: job-api publishes to Pub/Sub; any worker pod picks it up
                (loose coupling, retries, DLQ, horizontal scaling)
    """
    import httpx
    agent_url = settings.agent_runtime_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=600) as client:
            resp = await client.post(
                f"{agent_url}/analyze",
                json=message.model_dump(mode="json"),
                headers={
                    "X-Internal-Token": settings.internal_api_token,
                    "X-Trace-ID": message.trace_id,
                    "Content-Type": "application/json",
                },
            )
            resp.raise_for_status()
            log.info(
                "Agent-runtime HTTP call completed",
                job_id=message.job_id,
                status_code=resp.status_code,
            )
    except Exception as e:
        log.error(
            f"Agent-runtime HTTP call failed: {e}",
            job_id=message.job_id,
            agent_url=agent_url,
        )
        await update_job_status(message.job_id, {
            "status": "FAILED",
            "last_error": f"Agent runtime unreachable: {e}",
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })


async def publish_job(message: PubSubMessage) -> bool:
    """
    Publish a job message to Pub/Sub (or HTTP fallback for Lab 2).

    Priority:
      1. GCP Pub/Sub (Lab 3 docker-compose and Lab 4 production)
      2. Agent-runtime HTTP endpoint — background task (Lab 2 local dev)
      3. In-memory queue — last resort (nothing will consume it automatically)

    Returns True if the job was successfully dispatched.
    """
    publisher = get_pubsub_publisher()
    if publisher:
        try:
            topic_path = publisher.topic_path(
                settings.pubsub_project_id,
                settings.pubsub_topic_requests,
            )
            data = json.dumps(message.model_dump(mode="json"), default=str).encode("utf-8")
            future = publisher.publish(
                topic_path,
                data,
                job_id=message.job_id,
                trace_id=message.trace_id,
            )
            msg_id = future.result(timeout=10)
            log.info(
                "Job published to Pub/Sub",
                job_id=message.job_id,
                pubsub_msg_id=msg_id,
            )
            return True
        except Exception as e:
            log.error(f"Pub/Sub publish failed: {e}", job_id=message.job_id)

    agent_url = settings.agent_runtime_url
    if agent_url and agent_url not in ("", "http://localhost:8002"):
        try:
            asyncio.create_task(_call_agent_runtime_http(message))
            log.info(
                "Job dispatched to agent-runtime via HTTP (Lab 2 mode)",
                job_id=message.job_id,
                agent_url=agent_url,
            )
            return True
        except Exception as e:
            log.warning(f"Could not dispatch to agent-runtime: {e}", job_id=message.job_id)
    elif agent_url == "http://localhost:8002":
        try:
            asyncio.create_task(_call_agent_runtime_http(message))
            log.info(
                "Job dispatched to agent-runtime via HTTP (Lab 2 local mode)",
                job_id=message.job_id,
                agent_url=agent_url,
            )
            return True
        except Exception as e:
            log.warning(f"Could not dispatch to agent-runtime: {e}", job_id=message.job_id)

    _IN_MEMORY_QUEUE.append(message.model_dump(mode="json"))
    log.warning(
        "Job queued in-memory (no Pub/Sub, no agent-runtime reachable) — "
        "start agent-runtime with: python worker.py --mode http",
        job_id=message.job_id,
    )
    return False


async def check_rate_limit(user_id: str) -> bool:
    """
    Check if the user has exceeded their request rate limit.

    Teaching note:
      Redis stores a counter with TTL=60s per user.
      Key: rate_limit:{user_id}
      This is stateless from the application's perspective — any job-api
      pod can check any user's rate limit because Redis is shared.
      This is why you need a distributed cache, not just an in-memory dict.

      socket_timeout / socket_connect_timeout = 3s:
      Cloud Run does NOT have VPC access by default, so Memorystore Redis
      (10.x.x.x) is unreachable. Without timeouts the TCP SYN hangs forever,
      blocking the entire request. With 3s timeouts we fail-open in <3s.
    """
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(
            settings.redis_url, decode_responses=True,
            socket_timeout=3, socket_connect_timeout=3,
        )
        key = f"rate_limit:{user_id}"
        count = await r.incr(key)
        if count == 1:
            await r.expire(key, 60)
        await r.aclose()
        if count > settings.rate_limit_rpm:
            log.warning(
                "Rate limit exceeded",
                user_id=user_id,
                count=count,
                limit=settings.rate_limit_rpm,
            )
            return False
        return True
    except Exception:
        return True


class SubmitRequest(BaseModel):
    query: str = Field(..., min_length=3, max_length=2000)
    symbols: list = Field(default_factory=list)
    user_id: str = Field(default="anonymous")
    idempotency_key: Optional[str] = None


class JobSubmitResponse(BaseModel):
    job_id: str
    status: str
    message: str
    trace_id: str


async def find_existing_job(idempotency_key: str) -> Optional[str]:
    """Return existing job_id if the same idempotency_key was used before."""
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(
            settings.redis_url, decode_responses=True,
            socket_timeout=3, socket_connect_timeout=3,
        )
        existing = await r.get(f"idem:{idempotency_key}")
        await r.aclose()
        return existing
    except Exception:
        return None


async def store_idempotency(idempotency_key: str, job_id: str):
    """Store idempotency_key → job_id mapping (TTL: 24h)."""
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(
            settings.redis_url, decode_responses=True,
            socket_timeout=3, socket_connect_timeout=3,
        )
        await r.setex(f"idem:{idempotency_key}", 86400, job_id)
        await r.aclose()
    except Exception:
        pass


@app.middleware("http")
async def trace_middleware(request: Request, call_next):
    trace_id = request.headers.get("X-Trace-ID") or str(uuid.uuid4())
    set_correlation(trace_id=trace_id)
    response = await call_next(request)
    response.headers["X-Trace-ID"] = trace_id
    return response


@app.get("/health")
async def health():
    return {"status": "ok", "service": "job-api"}


@app.get("/ready")
async def ready():
    return {"status": "ready"}


@app.post("/jobs", response_model=JobSubmitResponse, status_code=202)
async def submit_job(body: SubmitRequest):
    """
    Submit an analysis request.

    Returns immediately with a job_id.
    The frontend polls GET /jobs/{job_id} for completion.

    Teaching note:
      HTTP 202 Accepted = "I got your request, processing is in progress"
      HTTP 200 OK       = "Here is your complete answer" (blocking)
      202 is correct for async jobs.
    """
    trace_id = get_trace_id()

    if body.idempotency_key:
        existing_id = await find_existing_job(body.idempotency_key)
        if existing_id:
            log.info(
                "Duplicate request — returning existing job",
                job_id=existing_id,
                idempotency_key=body.idempotency_key,
            )
            return JobSubmitResponse(
                job_id=existing_id,
                status="PENDING",
                message="Duplicate request — returning existing job",
                trace_id=trace_id,
            )

    allowed = await check_rate_limit(body.user_id)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit exceeded: max {settings.rate_limit_rpm} requests/minute",
        )

    guardrail = InputGuardrails(
        max_length=settings.guardrail_max_input_length,
        injection_detection=settings.guardrail_injection_detection,
    )
    allowed_input, guard_results = guardrail.is_allowed(body.query)
    if not allowed_input:
        blocked = [r for r in guard_results if not r.allowed]
        log.warning(
            "Input blocked by guardrails",
            check=blocked[0].check_name if blocked else "unknown",
            reason=blocked[0].reason if blocked else "",
        )
        raise HTTPException(
            status_code=400,
            detail={
                "error": "input_blocked",
                "check": blocked[0].check_name if blocked else "unknown",
                "reason": blocked[0].reason if blocked else "Input rejected by guardrails",
            },
        )

    analysis_request = AnalysisRequest(
        query=body.query,
        symbols=body.symbols,
        user_id=body.user_id,
        idempotency_key=body.idempotency_key,
    )
    job = JobRecord(
        request=analysis_request,
        status=JobStatus.PENDING,
    )

    log.info("Job created", job_id=job.job_id, query=body.query[:80])

    await save_job(job)

    message = PubSubMessage(
        job_id=job.job_id,
        request=analysis_request,
        trace_id=trace_id,
    )
    await publish_job(message)

    if body.idempotency_key:
        await store_idempotency(body.idempotency_key, job.job_id)

    log.info(
        "Job submitted successfully",
        job_id=job.job_id,
        trace_id=trace_id,
    )

    return JobSubmitResponse(
        job_id=job.job_id,
        status="PENDING",
        message="Job queued for processing",
        trace_id=trace_id,
    )


@app.get("/jobs/{job_id}")
async def get_job(job_id: str):
    """
    Get the current status of a job.

    Frontend polls this endpoint every 2 seconds.

    Teaching note:
      Alternative: use Firestore real-time listeners to PUSH updates to
      the frontend instead of polling. More efficient at scale.
      For the classroom demo, polling is simpler to explain.
    """
    job = await load_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    response = JobStatusResponse.from_job_record(job)
    return response.model_dump(mode="json")


@app.get("/jobs")
async def list_jobs(user_id: str = "anonymous", limit: int = 10):
    """List recent jobs for a user (for the frontend job history view)."""
    db = get_firestore()
    if db:
        try:
            docs = (
                db.collection("jobs")
                .where("request.user_id", "==", user_id)
                .order_by("created_at", direction="DESCENDING")
                .limit(limit)
                .stream()
            )
            jobs = []
            async for doc in docs:
                job = JobRecord.from_firestore(doc.to_dict())
                jobs.append(JobStatusResponse.from_job_record(job).model_dump(mode="json"))
            return jobs
        except Exception as e:
            log.error(f"list_jobs failed: {e}")

    jobs = [
        JobRecord.from_firestore(d)
        for d in _IN_MEMORY_JOBS.values()
        if d.get("request", {}).get("user_id") == user_id
    ]
    jobs.sort(key=lambda j: j.created_at, reverse=True)
    return [
        JobStatusResponse.from_job_record(j).model_dump(mode="json")
        for j in jobs[:limit]
    ]


@app.patch("/jobs/{job_id}")
async def update_job(
    job_id: str,
    body: Dict[str, Any],
    x_internal_token: str = Header(default=""),
):
    """
    Update job status (called by agent-runtime, not by users).

    Teaching note:
      This is an internal endpoint — only the agent-runtime should call it.
      Protected by X-Internal-Token header.
      In production, use Cloud IAM instead.
    """
    if settings.api_auth_enabled and x_internal_token != settings.internal_api_token:
        raise HTTPException(status_code=401, detail="Unauthorized")

    await update_job_status(job_id, body)
    log.info("Job updated", job_id=job_id, updates=list(body.keys()))
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=settings.is_local)
