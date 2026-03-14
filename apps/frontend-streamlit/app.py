"""
Frontend Streamlit App — Production version.

Teaching note (compare with original streamlit_crewai_app.py):
  WHAT CHANGED:
  1. Submits to the Job API (POST /jobs) and gets a job_id immediately.
     No more blocking the browser for 2 minutes.
  2. Polls GET /jobs/{job_id} every 2 seconds until COMPLETED.
  3. Shows live status: PENDING → RUNNING → COMPLETED.
  4. Displays tool trace, guardrail events, latency, and Langfuse link.
  5. Job history: see all recent jobs for your session.

  WHY this matters:
  - Users can submit 5 queries simultaneously (each gets its own job).
  - The browser can be closed and reopened — the job still runs.
  - Multiple users share the same worker pool (fair queueing).
"""
import os
import sys
import time
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

import requests
import streamlit as st

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
JOB_API_URL = os.getenv("JOB_API_URL", "http://localhost:8000")
LANGFUSE_HOST = os.getenv("LANGFUSE_HOST", "http://localhost:3000")
POLL_INTERVAL_SECONDS = 2
MAX_POLL_SECONDS = 300  # 5 minutes timeout

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Stock Agent — Production",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
.status-pending  { color: #FFA500; font-weight: bold; }
.status-running  { color: #1E90FF; font-weight: bold; }
.status-completed{ color: #28A745; font-weight: bold; }
.status-failed   { color: #DC3545; font-weight: bold; }
.job-card { border: 1px solid #ddd; border-radius: 8px; padding: 1rem; margin: 0.5rem 0; }
.guardrail-allow { color: #28A745; }
.guardrail-block { color: #DC3545; font-weight: bold; }
.guardrail-modify{ color: #FFA500; }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Session state initialization
# ---------------------------------------------------------------------------
if "user_id" not in st.session_state:
    st.session_state.user_id = f"user-{str(uuid.uuid4())[:8]}"
if "active_jobs" not in st.session_state:
    st.session_state.active_jobs = {}  # job_id -> last status
if "completed_jobs" not in st.session_state:
    st.session_state.completed_jobs = {}  # job_id -> result

# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def check_job_api() -> bool:
    try:
        r = requests.get(f"{JOB_API_URL}/health", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def submit_job(query: str, symbols: list, user_id: str) -> Optional[Dict]:
    """Submit a job to the Job API. Returns {job_id, status, trace_id} or None."""
    try:
        response = requests.post(
            f"{JOB_API_URL}/jobs",
            json={
                "query": query,
                "symbols": symbols,
                "user_id": user_id,
                "idempotency_key": str(uuid.uuid4()),
            },
            timeout=10,
        )
        if response.status_code in (200, 202):
            return response.json()
        else:
            detail = response.json().get("detail", response.text)
            st.error(f"Submission failed: {detail}")
            return None
    except Exception as e:
        st.error(f"Could not reach Job API: {e}")
        return None


def get_job_status(job_id: str) -> Optional[Dict]:
    """Poll the Job API for current status."""
    try:
        response = requests.get(f"{JOB_API_URL}/jobs/{job_id}", timeout=5)
        if response.status_code == 200:
            return response.json()
        return None
    except Exception:
        return None


def langfuse_link(trace_id: str) -> str:
    """Return a link to the Langfuse trace for this job."""
    return f"{LANGFUSE_HOST}/traces/{trace_id}"


# ---------------------------------------------------------------------------
# UI Components
# ---------------------------------------------------------------------------

def render_status_badge(status: str) -> str:
    css_class = {
        "PENDING": "status-pending",
        "RUNNING": "status-running",
        "COMPLETED": "status-completed",
        "FAILED": "status-failed",
    }.get(status, "")
    icons = {
        "PENDING": "⏳",
        "RUNNING": "🔄",
        "COMPLETED": "✅",
        "FAILED": "❌",
    }
    return f'<span class="{css_class}">{icons.get(status, "")} {status}</span>'


def render_tool_trace(tool_trace: list):
    if not tool_trace:
        return
    st.subheader("🔧 Tool Call Trace")
    st.caption(
        "Every MCP tool call made by the agents. "
        "In production, these are also visible as spans in Cloud Trace and Langfuse."
    )
    for i, call in enumerate(tool_trace, 1):
        success_icon = "✅" if call.get("success") else "❌"
        with st.expander(
            f"{i}. {success_icon} `{call.get('tool_name', '?')}` "
            f"— {call.get('duration_ms', 0):.0f}ms",
            expanded=False,
        ):
            col1, col2 = st.columns(2)
            with col1:
                st.json(call.get("arguments", {}))
            with col2:
                if call.get("error"):
                    st.error(call["error"])
                elif call.get("result_preview"):
                    st.code(call["result_preview"][:300])


def render_guardrail_events(events: list):
    if not events:
        return
    st.subheader("🛡️ Guardrail Decisions")
    st.caption(
        "Guardrails run at 3 layers: Input (before crew), Tool (per call), Output (final answer). "
        "BLOCK = request stopped. MODIFY = content sanitized. ALLOW = passed."
    )
    for event in events:
        decision = event.get("decision", "ALLOW")
        layer = event.get("layer", "")
        check = event.get("check_name", "")
        reason = event.get("reason", "")
        css = {
            "ALLOW": "guardrail-allow",
            "BLOCK": "guardrail-block",
            "MODIFY": "guardrail-modify",
        }.get(decision, "")
        st.markdown(
            f'<span class="{css}">{decision}</span> | '
            f"**{layer}** → `{check}` | {reason}",
            unsafe_allow_html=True,
        )


def render_usage(usage: Optional[Dict]):
    if not usage:
        return
    st.subheader("💰 Cost & Usage")
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Tokens", f"{usage.get('total_tokens', 0):,}")
    col2.metric("Est. Cost", f"${usage.get('estimated_cost_usd', 0):.4f}")
    col3.metric("Input / Output",
                f"{usage.get('total_input_tokens', 0):,} / {usage.get('total_output_tokens', 0):,}")


def render_job_result(job: Dict):
    """Render the complete result of a finished job."""
    status = job.get("status", "")
    job_id = job.get("job_id", "")
    latency = job.get("latency_seconds")

    # --- Status header ---
    st.markdown(render_status_badge(status), unsafe_allow_html=True)

    if latency:
        st.caption(f"⏱️ Completed in {latency:.1f}s")

    if status == "COMPLETED":
        # --- Main report ---
        st.subheader("📋 Analysis Report")
        result = job.get("result", "")
        if result:
            st.markdown(result)

            # Download
            st.download_button(
                "📥 Download Report",
                data=result,
                file_name=f"stock_analysis_{job_id[:8]}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md",
                mime="text/markdown",
            )
        else:
            st.warning("No result content found.")

        # --- Tool trace ---
        render_tool_trace(job.get("tool_trace", []))

        # --- Guardrail events ---
        render_guardrail_events(job.get("guardrail_events", []))

        # --- Usage ---
        render_usage(job.get("usage"))

        # --- Langfuse link ---
        trace_id = job.get("trace_id", "")
        if trace_id:
            st.info(
                f"🔍 **LLM Trace**: View all Gemini calls for this job in "
                f"[Langfuse]({langfuse_link(trace_id)})"
            )

    elif status == "FAILED":
        st.error(f"Job failed: {job.get('error', 'Unknown error')}")
        render_guardrail_events(job.get("guardrail_events", []))


# ---------------------------------------------------------------------------
# Main App
# ---------------------------------------------------------------------------

def main():
    # --- Header ---
    st.title("📈 Stock Agent — Production")
    st.markdown(
        "**Async AI-powered stock analysis** | "
        "CrewAI + Gemini + MCP + GCP"
    )

    # --- Sidebar ---
    with st.sidebar:
        st.header("⚙️ Configuration")

        # API status
        st.subheader("🔗 Service Status")
        api_ok = check_job_api()
        if api_ok:
            st.success("✅ Job API Connected")
        else:
            st.error(f"❌ Job API unreachable ({JOB_API_URL})")
            st.info("Run `make up` to start the local stack.")

        st.markdown("---")

        # User ID (for rate limiting display)
        st.caption(f"Session ID: `{st.session_state.user_id[:16]}...`")

        # Links
        st.subheader("🔗 Observability")
        st.markdown(f"[📊 Langfuse LLM Traces]({LANGFUSE_HOST})")
        st.markdown(f"[📚 Job API Docs]({JOB_API_URL}/docs)")

        st.markdown("---")

        # Architecture reminder
        with st.expander("🏗️ Architecture"):
            st.markdown("""
**This demo shows Stage 4:**
```
Streamlit → Job API → Pub/Sub
              ↓
         Agent Runtime
         (CrewAI workers)
              ↓
         MCP Server
         (Gemini + yfinance)
              ↓
         Firestore + Redis
```
**Key patterns:**
- Async jobs (202 Accepted)
- Pub/Sub for decoupling
- Redis caching
- 3-layer guardrails
- Langfuse LLM tracing
""")

    # --- Query submission ---
    st.header("🔍 Submit Analysis")

    col1, col2 = st.columns([3, 1])
    with col1:
        query = st.text_area(
            "What would you like to analyse?",
            placeholder=(
                "e.g. 'Summarise the latest data for AAPL' or "
                "'Compare NVDA vs AMD technical indicators' or "
                "'Is TSLA oversold based on RSI?'"
            ),
            height=100,
            key="query_input",
        )
    with col2:
        symbols_raw = st.text_input(
            "Symbols (optional)",
            placeholder="AAPL NVDA",
            help="Space-separated tickers. If empty, agent will infer from query.",
        )
        symbols = [s.upper().strip() for s in symbols_raw.split() if s.strip()]

    submit_col, clear_col, _ = st.columns([1, 1, 3])
    with submit_col:
        submit_btn = st.button(
            "🚀 Submit",
            type="primary",
            disabled=not api_ok or not query.strip(),
        )
    with clear_col:
        if st.button("🗑️ Clear"):
            st.session_state.active_jobs = {}
            st.session_state.completed_jobs = {}
            st.rerun()

    if submit_btn and query.strip():
        with st.spinner("Submitting job..."):
            result = submit_job(query.strip(), symbols, st.session_state.user_id)
        if result:
            job_id = result["job_id"]
            st.session_state.active_jobs[job_id] = {
                "status": "PENDING",
                "query": query.strip(),
                "submitted_at": datetime.now().isoformat(),
                "trace_id": result.get("trace_id", ""),
            }
            st.success(f"✅ Job submitted! ID: `{job_id}`")
            st.caption(
                "⚡ Your request is queued. Results appear below as they complete. "
                "You can submit more queries while this one runs."
            )

    # --- Active jobs ---
    if st.session_state.active_jobs:
        st.header("📊 Active Jobs")
        st.caption("Polling every 2 seconds...")

        for job_id, job_info in list(st.session_state.active_jobs.items()):
            with st.container():
                st.markdown(f"---")
                st.markdown(f"**Query:** {job_info.get('query', '')[:80]}")
                st.markdown(f"**Job ID:** `{job_id}`")

                # Poll for status
                current = get_job_status(job_id)
                if current:
                    status = current.get("status", "PENDING")
                    st.markdown(render_status_badge(status), unsafe_allow_html=True)

                    if status in ("COMPLETED", "FAILED"):
                        # Move to completed
                        st.session_state.completed_jobs[job_id] = current
                        del st.session_state.active_jobs[job_id]
                        st.rerun()
                    else:
                        # Still running — show progress indicator
                        if status == "RUNNING":
                            st.info("🔄 Agents are working... Tool calls in progress")

        # Auto-refresh
        time.sleep(POLL_INTERVAL_SECONDS)
        st.rerun()

    # --- Completed jobs ---
    if st.session_state.completed_jobs:
        st.header("✅ Completed Jobs")

        for job_id, job_data in reversed(list(st.session_state.completed_jobs.items())):
            with st.expander(
                f"{'✅' if job_data.get('status') == 'COMPLETED' else '❌'} "
                f"{job_data.get('request', {}).get('query', 'Unknown query')[:60]}... "
                f"| `{job_id[:12]}` | {(job_data.get('latency_seconds') or 0):.0f}s",
                expanded=(list(st.session_state.completed_jobs.keys())[-1] == job_id),
            ):
                render_job_result(job_data)

    # --- Footer ---
    st.markdown("---")
    st.caption(
        "🤖 Powered by CrewAI + Gemini (Vertex AI) + GCP Pub/Sub + Firestore | "
        "📊 LLM traces: Langfuse | 🔍 Infra traces: Cloud Trace"
    )


if __name__ == "__main__":
    main()
