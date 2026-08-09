# Workshop 02 — The 4 Labs: From Single Script to Cloud Platform

**Duration:** 4 × 45 min  
**Audience:** Software engineers familiar with Python and basic cloud concepts

---

## Overview — Architecture Progression

Each lab builds on the previous one, introducing exactly one new layer of complexity:

```
Lab 1 ── Sync local         │ Single process, blocking UI
Lab 2 ── Async HTTP         │ Service boundaries, 202/poll pattern, no Docker
Lab 3 ── Pub/Sub + Docker   │ Full local containerised stack with message queue
Lab 4 ── Real GCP           │ Managed cloud services, Vertex AI, auto-scaling
```

The core business logic (CrewAI agents, MCP tools, yfinance) is identical across all labs.
What changes is **how** the components are wired together.

---

## Pre-Work (all labs)

> **Windows users:** everything below assumes a POSIX shell. Set up WSL2 first and run
> all commands from inside Ubuntu — see [../../WINDOWSREADME.md](../../WINDOWSREADME.md).

```bash
git clone https://github.com/zviba/mcp_stocks_demo_crewai_exercise.git
cd mcp_stocks_demo_crewai_exercise
cp .env.example .env
# Set GEMINI_API_KEY in .env (free key: https://aistudio.google.com/apikey)
# Or set LLM_PROVIDER=openai and OPENAI_API_KEY instead — every lab uses the
# same provider switcher (packages/shared-config), so either works everywhere.
```

---

## Lab 1 — Sync Local (Single Process)

### What You'll Learn
- How CrewAI agents, tasks, and tools are wired together
- What MCP (Model Context Protocol) is and how tools expose data
- Why a synchronous architecture fails at scale

### Architecture

```
Browser ──► streamlit_crewai_app.py  (blocks until done, ~1-2 min)
                │
                │  direct Python import
                ▼
            agents.py
            (CrewAI: 4 agents, 4 tasks, sequential crew)
                │
                │  direct Python import
                ▼
            mcp_server.py
            (MCP tools: search_symbols, get_quote, get_indicators…)
                │
                ▼
            datasource.py → yfinance → Yahoo Finance API
                │
            api.py  (FastAPI on :8001, used only for health check)
```

**Key files:**

| File | Role |
|------|------|
| `streamlit_crewai_app.py` | Web UI, calls `run_crewai_analysis()` directly |
| `agents.py` | Agent + task definitions, `crewai.LLM` — Gemini, Vertex AI, or OpenAI (sidebar picker) |
| `mcp_server.py` | `@tool`-decorated functions, `get_tools_by_names()` |
| `datasource.py` | yfinance wrapper |
| `api.py` | FastAPI `/health` endpoint (checked by the Streamlit app) |
| `requirements.txt` | Root-level deps: crewai, streamlit, yfinance, fastapi |

### Run Instructions

**Terminal 1 — MCP API (health check endpoint):**
```bash
pip install -r requirements.txt
uvicorn api:app --host 127.0.0.1 --port 8001
```

**Terminal 2 — Streamlit UI:**
```bash
streamlit run streamlit_crewai_app.py
```

Open http://localhost:8501. In the sidebar, pick an **LLM Provider** — Google AI Studio, Vertex AI (ADC), or OpenAI — enter that provider's API key (Vertex AI needs none), type `AAPL`, click **Start Analysis**.

### What to Observe
- The browser tab spins for 1-2 minutes while the crew runs
- Open a second tab → try to submit another query → it also blocks
- There is no retry if the analysis fails halfway
- You can't see which tool call took the longest
- The report-writing task has CrewAI's built-in `HallucinationGuardrail` attached (visible in the result's `crewai_guardrail` field) — in the open-source `crewai` package it's a no-op that passes every output; the actual check is the deterministic `guardrail` (grounding) field next to it, built from the real tool-call evidence

### Discussion Questions
1. What happens if 10 students submit at the same time?
2. Where is the API key stored? (Answer: Streamlit session state — gone on refresh)
3. What happens if the browser closes during analysis?
4. Can you tell if the agents actually called the tools, or just made up data?

---

## Lab 2 — Async HTTP (Service Boundaries, No Docker)

### What You'll Learn
- The `HTTP 202 Accepted` async pattern (submit → poll)
- Why service boundaries matter even on localhost
- Guardrails at the API layer
- The cost of tight HTTP coupling (vs. Pub/Sub in Lab 3)

### Architecture

```
Browser ──► apps/frontend-streamlit/app.py
            (submits job, gets job_id immediately, polls every 2s)
                │  POST /jobs → 202 Accepted
                ▼
            apps/job-api/main.py  (:8000)
            (validates, stores PENDING, dispatches to agent-runtime)
                │  asyncio.create_task → POST /analyze (background)
                ▼
            apps/agent-runtime/worker.py --mode http  (:8002)
            (runs CrewAI crew, PATCHes job status back to job-api)
                │  POST /quote /indicators /events…
                ▼
            apps/mcp-server/server.py  (:8001)
            (production MCP server: auth, logging, health checks)
                │
                ▼
            datasource.py → yfinance
```

**Key differences from Lab 1:**

| Aspect | Lab 1 | Lab 2 |
|--------|-------|-------|
| Execution | Blocking (browser waits) | Async (202 → poll) |
| Agent invocation | Direct Python import | HTTP POST /analyze |
| Tool calls | Direct Python import | HTTP POST /quote, /indicators… |
| Job state | Streamlit session only | In-memory dict in job-api |
| Concurrency | 1 request at a time | Multiple jobs simultaneously |

**Key files:**

| File | Role |
|------|------|
| `apps/frontend-streamlit/app.py` | Production UI with async polling |
| `apps/job-api/main.py` | Job gateway: validate, persist, dispatch |
| `apps/agent-runtime/worker.py` | Worker in HTTP mode (`--mode http`) |
| `apps/mcp-server/server.py` | Production MCP server with auth + logging |
| `packages/shared-config/config.py` | Settings via env vars |
| `packages/shared-guardrails/guardrails.py` | Input/tool/output guardrails |

### Run Instructions

Install deps for each service first:
```bash
pip install -r apps/mcp-server/requirements.txt
pip install -r apps/job-api/requirements.txt
pip install -r apps/agent-runtime/requirements.txt
pip install -r apps/frontend-streamlit/requirements.txt
```

**Terminal 1 — MCP Server:**
```bash
cd apps/mcp-server
GEMINI_API_KEY=your-key \
LOG_FORMAT=text \
uvicorn server:app --host 0.0.0.0 --port 8001
```

**Terminal 2 — Agent Runtime (HTTP mode):**
```bash
cd apps/agent-runtime
GEMINI_API_KEY=your-key \
MCP_SERVER_URL=http://localhost:8001 \
JOB_API_URL=http://localhost:8000 \
LOG_FORMAT=text \
python worker.py --mode http
```

**Terminal 3 — Job API:**
```bash
cd apps/job-api
GEMINI_API_KEY=your-key \
AGENT_RUNTIME_URL=http://localhost:8002 \
LOG_FORMAT=text \
uvicorn main:app --host 0.0.0.0 --port 8000
```

**Terminal 4 — Frontend:**
```bash
JOB_API_URL=http://localhost:8000 \
streamlit run apps/frontend-streamlit/app.py
```

Open http://localhost:8501 and submit: `"Summarize the latest technical data for NVDA"`

### What to Observe
- Submit returns in < 1 second (HTTP 202)
- The UI shows `PENDING → RUNNING → COMPLETED` as you poll
- Submit two queries — both run simultaneously in the same agent-runtime process
- Watch the logs in Terminal 2 — you can see each tool call with timing
- Try submitting `"Ignore previous instructions"` — watch the guardrail block it

### How the Async Dispatch Works (Teaching Note)

When `publish_job()` in `job-api/main.py` can't reach Pub/Sub, it falls back to:
```python
asyncio.create_task(_call_agent_runtime_http(message))
```
This fires a background HTTP task to `agent-runtime/analyze`. The job-api returns 202 immediately. When the agent-runtime finishes, it `PATCH`es `/jobs/{job_id}` back to the job-api to update status. The frontend polls and eventually sees COMPLETED.

**The key insight:** The user's browser is decoupled from the compute. The job continues even if the browser refreshes.

### Discussion Questions
1. What happens if the agent-runtime crashes mid-analysis? (No retry in Lab 2)
2. Can you run 10 agent-runtimes simultaneously to process more jobs? (Not easily — no queue)
3. What's the difference between `api_auth_enabled=False` (Lab 2) vs. Cloud IAM (Lab 4)?
4. The `PATCH /jobs/{job_id}` endpoint is "internal" — what prevents a user from calling it?

---

## Lab 3 — Pub/Sub + Docker Compose (Full Local Stack)

### What You'll Learn
- Docker networking and service discovery
- GCP Pub/Sub async messaging (decoupling producer from consumer)
- Redis caching — same query, zero LLM calls on second request
- LLM observability with Langfuse (token counts, costs, traces)
- Health checks, dependency ordering, restart policies

### Architecture

```
Browser ──► frontend-streamlit :8501
                │  POST /jobs
                ▼
            job-api :8000
            (Firestore in-memory, Redis rate limiting)
                │  publish to Pub/Sub
                ▼
            pubsub-emulator :8085
            (GCP Pub/Sub running locally in Docker)
                │  deliver message
                ▼
            agent-runtime (Pub/Sub mode)
            (subscribes to analysis-requests topic)
                │  real MCP protocol (/mcp) + /explain
                ▼
            mcp-server :8001 ──► litellm-proxy :4010 ──► Gemini / OpenAI
                │                      │
                ▼                      ▼
            Redis :6379          Langfuse :3000 ◄── native litellm callback
            (result cache,             ▲
             TTL 1h)                postgres
                              (shared: langfuse + litellm databases)
```

LiteLLM Proxy is the *default* LLM route in this lab (`LITELLM_BASE_URL` is set for
you in `docker-compose.yml`) — every Gemini/OpenAI call from agent-runtime and
mcp-server's `/explain` goes through it, and it reports each one to Langfuse itself
(`success_callback: ["langfuse"]` in `docker/litellm/config.yaml`) with no
application code involved.

**Per-request provider override.** The frontend sidebar calls `GET /providers`
on job-api to show which vendors are configured server-side, and lets a student
pick a different one — with their own API key — for a single run, without
touching `.env`. `POST /jobs` accepts optional `provider`/`api_key` fields that
flow through job-api → agent-runtime's `get_llm(provider=, api_key=)`; a
request-level key bypasses the LiteLLM proxy and calls the vendor directly
(the proxy's `config.yaml` only knows the server's own keys), and the key
itself is never persisted to Firestore or echoed back by `GET /jobs/{id}` —
only `provider` is (see the note on `AnalysisRequest`/`PubSubMessage` in
`packages/shared-models/models.py`).

### Docker Compose Stack

| Container | Image | Port | Purpose |
|-----------|-------|------|---------|
| `redis` | redis:7-alpine | 6379 | Cache + rate limiting |
| `pubsub-emulator` | google-cloud-cli | 8085 | GCP Pub/Sub locally |
| `pubsub-init` | google-cloud-cli | — | Creates topics/subscriptions (runs once) |
| `postgres` | postgres:16 | — | Shared DB: Langfuse's data + a separate `litellm` database for LiteLLM's admin UI |
| `langfuse` | langfuse/langfuse | 3000 | LLM observability UI |
| `litellm-proxy` | built locally (`docker/litellm/`) | 4010 | Routes every LLM call, traces to Langfuse; `/ui` for spend/usage (admin / `LITELLM_MASTER_KEY`) |
| `mcp-server` | built locally | 8001 | Stock data tools (real MCP + REST) |
| `job-api` | built locally | 8000 | Async job gateway |
| `agent-runtime` | built locally | 8002 | CrewAI workers |
| `frontend-streamlit` | built locally | 8501 | Web UI |

### Run Instructions

```bash
# Ensure .env has GEMINI_API_KEY set (or LLM_PROVIDER=openai + OPENAI_API_KEY)
cat .env | grep -E "GEMINI_API_KEY|OPENAI_API_KEY"

# Start the full stack (builds images on first run ~5 min)
make lab3-up

# Or directly:
docker compose -f docker/docker-compose.yml up --build -d

# Watch startup logs
make logs
```

**Startup order** (watch it happen in logs):
```
1. redis → healthy
2. pubsub-emulator → healthy
3. pubsub-init → creates topics → exits (service_completed_successfully)
4. postgres → healthy
5. langfuse → healthy
6. litellm-proxy → healthy (depends on langfuse + postgres)
7. mcp-server → healthy
8. job-api → healthy (depends on redis + pubsub)
9. agent-runtime → starts (depends on mcp-server + job-api + redis + pubsub-init)
10. frontend-streamlit → healthy (depends on job-api)
```

### Service URLs

| Service | URL |
|---------|-----|
| Streamlit UI | http://localhost:8501 |
| Job API docs | http://localhost:8000/docs |
| MCP Server docs | http://localhost:8001/docs |
| MCP Server (real MCP) | http://localhost:8001/mcp/ |
| LiteLLM Proxy | http://localhost:4010 |
| Langfuse UI | http://localhost:3000 |
| Redis CLI | `docker exec -it stock-agent-redis redis-cli` |

**Langfuse login:** admin@example.com / admin123

### What to Observe

**1. Submit two queries simultaneously:**
```
Tab 1: "Analyse AAPL"
Tab 2: "Compare NVDA vs AMD indicators"
```
Both get unique job IDs and process in parallel.

**2. Follow the trace_id through all services:**
```bash
make logs | grep '"trace_id"'
```
You'll see the same `trace_id` in job-api, agent-runtime, and mcp-server logs for the same request.

**3. Redis caching — submit the same query twice:**
```bash
# After first request completes:
docker exec -it stock-agent-redis redis-cli keys "analysis:*"
```
The second identical request returns in < 100ms with 0 LLM calls.

**4. Langfuse LLM traces (via LiteLLM's native callback):**
Open http://localhost:3000 → Traces → click any trace.
- See every LLM call: prompt, response, token count, cost — reported by litellm-proxy
  itself (`success_callback: ["langfuse"]`), not by application code
- Compare `research_agent` (main tier) vs `report_agent` (strong tier)
- The `report_agent` uses the STRONG tier model — it runs once and produces the final report

**4b. LiteLLM's own view of the same calls:**
```bash
curl -s http://localhost:4010/health/liveliness
```
`docker/litellm/config.yaml`'s `general_settings` sets a basic daily budget
(`max_budget`) and a parallel-request cap — this is the "blocking requests" example:
push past either and the proxy starts returning `429`s instead of forwarding to the
vendor.

**5. Scale agent workers:**
```bash
docker compose -f docker/docker-compose.yml up --scale agent-runtime=3 -d
```
Three workers will now compete for Pub/Sub messages. Submit 5 queries — they'll be distributed.

### Key Concept: Why Pub/Sub > Direct HTTP

| Aspect | Lab 2 (HTTP) | Lab 3 (Pub/Sub) |
|--------|-------------|-----------------|
| Retry on failure | ❌ Manual | ✅ Automatic (3 attempts) |
| Dead letter queue | ❌ No | ✅ `analysis-dlq` topic |
| Worker crash | Job lost | Message redelivered |
| Scale workers | Restart needed | `--scale agent-runtime=N` |
| Job ordering | Depends on timing | FIFO within subscriber |
| Worker languages | Must be Python | Any language with Pub/Sub SDK |

### Cleanup
```bash
make lab3-down    # stop all containers (keeps volumes)
make clean        # stop + remove volumes + images
```

---

## Lab 4 — Full GCP Production

### What You'll Learn
- Infrastructure as Code with Terraform
- Cloud Run (serverless containers, scale to zero)
- GKE Autopilot (serverless Kubernetes for stateful workers)
- Workload Identity (no API keys in containers — Gemini via Vertex AI)
- Secret Manager (secrets injected at runtime, never in code)
- Cloud Logging + Cloud Trace (distributed tracing across services)

### Architecture

```
                    ┌─────────────────────────────┐
   Browser ─────►  │  Cloud Run: frontend         │  (HTTPS, global CDN)
                    └──────────────┬──────────────┘
                                   │  POST /jobs
                    ┌──────────────▼──────────────┐
                    │  Cloud Run: job-api          │  (autoscale 0→N)
                    │  Firestore: job state        │
                    │  Memorystore Redis: rate limit│
                    └──────────────┬──────────────┘
                                   │  publish
                    ┌──────────────▼──────────────┐
                    │  GCP Pub/Sub (managed)       │  (durable, at-least-once)
                    └──────────────┬──────────────┘
                                   │  subscribe
                    ┌──────────────▼──────────────┐
                    │  GKE Autopilot: agent-runtime│  (HPA scales on queue depth)
                    │  Workload Identity → Vertex AI│  (no API key needed!)
                    └──────────────┬──────────────┘
                                   │  POST /quote /indicators…
                    ┌──────────────▼──────────────┐
                    │  Cloud Run: mcp-server       │  (autoscale 0→N)
                    │  Vertex AI: gemini-3.5-flash │  (LLM_PROVIDER=vertex_ai)
                    └─────────────────────────────┘

Secret Manager ── secrets injected into Cloud Run + GKE as env vars
Artifact Registry ── Docker images built by Cloud Build / GitHub Actions
Cloud Logging ── structured JSON logs from all services
Cloud Trace ── distributed traces via X-Trace-ID header propagation
Langfuse on Cloud SQL ── LLM token traces (optional)
```

### Prerequisites

```bash
# Install tools
brew install terraform google-cloud-sdk

# Authenticate
gcloud auth login
gcloud auth application-default login

# Set your project
export GCP_PROJECT=your-project-id
export GCP_REGION=us-central1
```

### Step-by-Step Deployment

**Step 1 — One-time setup (run once per GCP project):**
```bash
make setup-gcp GCP_PROJECT=$GCP_PROJECT GCP_REGION=$GCP_REGION
```
This script:
1. Enables all required GCP APIs
2. Provisions full infrastructure with Terraform (~5 min):
   - GKE Autopilot cluster (`agent-cluster`)
   - Memorystore Redis (`stock-agent-cache`)
   - GCP Pub/Sub topics + subscription
   - Firestore database
   - Cloud SQL PostgreSQL (for Langfuse)
   - Artifact Registry (`stock-agent`)
   - Service accounts with least-privilege IAM
   - Secret Manager secrets
3. Builds + pushes all Docker images
4. Deploys all 4 services to Cloud Run
5. Deploys agent-runtime workers to GKE Autopilot
6. Prints all service URLs

**Step 2 — Add your Gemini API key to Secret Manager:**
```bash
# Option A: Use API key (google_ai_studio) — passing --gemini-api-key to
# setup-gcp.sh also seeds it into litellm-proxy alongside its always-on
# Vertex AI route (see Option B).
echo -n "your-gemini-api-key" | gcloud secrets versions add gemini-api-key \
  --data-file=- --project=$GCP_PROJECT

# Option B: Use Vertex AI (RECOMMENDED — no API key needed on GCP)
# Set LLM_PROVIDER=vertex_ai in the deployment and skip this step entirely.
# litellm-proxy has its own service account (litellm-proxy@..., granted
# roles/aiplatform.user by infra/terraform/main.tf) and a vertex-* entry
# in its model_list (docker/litellm/config.yaml) with no api_key — Google's
# client library falls back to that attached identity automatically, the
# same no-key pattern mcp-server/agent-runtime already use for their own
# direct Vertex AI calls. Every service routes through litellm-proxy
# unconditionally now (LITELLM_BASE_URL is always set), so Vertex AI calls
# get the same Langfuse tracing + budget/parallel-request caps as the
# API-key providers, not just Google AI Studio and OpenAI.
```

**Step 3 — Verify deployment:**
```bash
make show-urls GCP_PROJECT=$GCP_PROJECT GCP_REGION=$GCP_REGION
```

### The Critical Shift: API Key → Workload Identity

In Labs 1-3, agents authenticate to Gemini with an API key:
```python
# packages/shared-config/config.py
LLM_PROVIDER=google_ai_studio  # uses GEMINI_API_KEY env var
```

In Lab 4, set `LLM_PROVIDER=vertex_ai` in the deployment. The GKE pod's Workload Identity
service account has `roles/aiplatform.user` — no key in the container at all:
```python
# packages/shared-config/config.py → get_llm()
if settings.use_vertex_ai:
    from langchain_google_vertexai import ChatVertexAI
    return ChatVertexAI(model_name=model_name, project=settings.gcp_project)
    # No API key — Workload Identity handles auth transparently
```

### Observability in Production

**Cloud Logging** — all services emit structured JSON:
```bash
gcloud logging read 'resource.type="cloud_run_revision"' \
  --project=$GCP_PROJECT --limit=50 --format=json | jq '.[].jsonPayload'
```

**Cloud Trace** — follow a request across services:
- Open https://console.cloud.google.com/traces?project=$GCP_PROJECT
- Every service passes `X-Trace-ID` header → Cloud Trace correlates spans

**Langfuse** — LLM token traces:
- Deployed on Cloud SQL
- Access URL shown in `make show-urls`
- See every Gemini prompt + response + token count across all jobs

### Model Routing (Cost Optimisation)

```
FAST   → gemini-3.1-flash-lite  ← guardrail checks (cheap, near-free)
MAIN   → gemini-3.5-flash       ← research, technical, sector agents
STRONG → gemini-3.1-pro-preview         ← report synthesis (once per job, high quality)
```

Configured in `packages/shared-config/config.py` via env vars:
```bash
GEMINI_FAST_MODEL=gemini-3.1-flash-lite
GEMINI_MAIN_MODEL=gemini-3.5-flash
GEMINI_STRONG_MODEL=gemini-3.1-pro-preview
```

### Cost Control

**Scale to zero when not teaching:**
```bash
# Cloud Run scales to 0 automatically when idle (no cost)
# GKE Autopilot: destroy to stop billing
make infra-down GCP_PROJECT=$GCP_PROJECT GCP_REGION=$GCP_REGION
# Keeps: Firestore (free tier), Pub/Sub (free tier), Artifact Registry (minimal)
# Destroys: GKE cluster, Redis, Cloud SQL (~$0.50/hr when idle)
```

**Scale workers for a load test:**
```bash
make scale-workers REPLICAS=5
# GKE Autopilot provisions exactly the CPU/memory needed — pay per pod
```

### CI/CD (GitHub Actions)
The `.github/workflows/ci-cd.yml` pipeline:
1. Runs unit tests (`make test`)
2. Lints code (`make lint`)
3. Builds Docker images
4. Pushes to Artifact Registry
5. Deploys to Cloud Run on every push to `main`

---

## Comparison Summary

| Feature | Lab 1 | Lab 2 | Lab 3 | Lab 4 |
|---------|-------|-------|-------|-------|
| UI blocks during analysis | ✅ Yes | ❌ No | ❌ No | ❌ No |
| Concurrent jobs | ❌ No | ✅ Yes | ✅ Yes | ✅ Yes |
| Job survives browser close | ❌ No | ✅ Yes | ✅ Yes | ✅ Yes |
| Job retry on failure | ❌ No | ❌ No | ✅ Pub/Sub 3× | ✅ Pub/Sub 3× |
| Result caching | ❌ No | ❌ No | ✅ Redis | ✅ Redis |
| LLM traces | ❌ No | ❌ No | ✅ Langfuse | ✅ Langfuse |
| Distributed tracing | ❌ No | ❌ No | ❌ No | ✅ Cloud Trace |
| Scale workers | ❌ No | ❌ No | `--scale N` | HPA auto |
| API keys in code | ⚠️ Sidebar | ⚠️ .env file | ⚠️ .env file | ✅ Secret Manager |
| LLM auth | API key (OpenAI or Gemini) | API key | API key, via LiteLLM Proxy | Workload Identity (or LiteLLM Proxy) |
| LLM request limits/tracing | ❌ No | ❌ No | ✅ LiteLLM Proxy | ✅ LiteLLM Proxy (opt-in) |
| MCP protocol | Real MCP | Real MCP | Real MCP | Real MCP |
| Cost to run | Free | Free | Free | ~$0.50/hr idle |

---

## Next Workshop Ideas

- **Workshop 03:** Load testing — use Locust to send 50 concurrent requests, watch GKE HPA scale
- **Workshop 04:** Guardrail deep-dive — add a new check, see it trigger in all 3 layers
- **Workshop 05:** Model evaluation — use Langfuse Evals to score report quality across models
- **Workshop 06:** Adding a new MCP tool — add `/news` endpoint, wire it to a new agent
