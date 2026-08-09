# Architecture Overview — Stock Agent Platform

## System Diagram (Stage 4: Production)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              User Browser                                    │
└─────────────────────────────────┬───────────────────────────────────────────┘
                                  │ HTTP
┌─────────────────────────────────▼───────────────────────────────────────────┐
│                    FRONTEND (Cloud Run)                                      │
│                    Streamlit — apps/frontend-streamlit/app.py                │
│                                                                              │
│  1. Submit query → POST /jobs                                                │
│  2. Show job_id (returned in <100ms)                                         │
│  3. Poll GET /jobs/{id} every 2s                                             │
│  4. Display result + tool trace + guardrail events + Langfuse link           │
└─────────────────────────────────┬───────────────────────────────────────────┘
                                  │ HTTP POST /jobs (202 Accepted)
┌─────────────────────────────────▼───────────────────────────────────────────┐
│                     JOB API (Cloud Run)                                      │
│                     FastAPI — apps/job-api/main.py                           │
│                                                                              │
│  Pattern: Accept → Validate → Persist → Publish → Return                    │
│                                                                              │
│  ┌──────────────┐    ┌─────────────┐    ┌─────────────────────────────────┐ │
│  │ Rate Limiter │    │ Idempotency │    │ Input Guardrails (Layer 1)      │ │
│  │  (Redis)     │    │  (Redis)    │    │  - max length                   │ │
│  └──────────────┘    └─────────────┘    │  - injection detection          │ │
│                                         └─────────────────────────────────┘ │
│  ┌──────────────────────┐    ┌──────────────────────────────────────────┐   │
│  │ Firestore            │    │ Pub/Sub Topic: analysis-requests         │   │
│  │ jobs/{job_id} = {    │    │ (message: PubSubMessage JSON)            │   │
│  │   status: PENDING    │    └──────────────────────────────────────────┘   │
│  │   request: {...}     │                                                    │
│  │ }                    │                                                    │
│  └──────────────────────┘                                                    │
└─────────────────────────────────────────────────────────────────────────────┘
                                  │ Pub/Sub push/pull
┌─────────────────────────────────▼───────────────────────────────────────────┐
│                  AGENT RUNTIME (GKE Autopilot)                               │
│                  CrewAI Workers — apps/agent-runtime/worker.py               │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  GuardrailPipeline (Layer 1 input re-check)                         │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  Redis Cache Check → hit? return immediately without LLM call       │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  CrewAI Crew (4 agents, sequential)                                  │    │
│  │                                                                       │    │
│  │  Research Agent ──────────┐  (gemini-3.5-flash)                     │    │
│  │    search_symbols          │                                          │    │
│  │    latest_quote           ▼                                          │    │
│  │    price_series    Technical Agent ───┐  (gemini-3.5-flash)         │    │
│  │                      indicators       │                               │    │
│  │                      detect_events   ▼                               │    │
│  │                      explain  Sector Agent ───┐  (gemini-3.5-flash) │    │
│  │                               compare peers  ▼                      │    │
│  │                                       Report Agent  (gemini-3.1-pro-preview)│    │
│  │                                       synthesize                     │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                   │ Each tool call goes through Layer 2 (Tool guardrails)    │
│                   │ and is traced to Langfuse                                │
│  ┌────────────────▼────────────────────────────────────────────────────┐    │
│  │  GuardrailPipeline (Layer 3 output check)                           │    │
│  │    - redact secrets                                                  │    │
│  │    - flag price predictions                                          │    │
│  │    - add financial disclaimer                                        │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
│  → Firestore: update jobs/{job_id} = {status: COMPLETED, result: ...}       │
│  → Redis: cache result (TTL=1h)                                              │
└─────────────────────────────────┬───────────────────────────────────────────┘
                                  │ Real MCP protocol (Streamable HTTP) at /mcp
                                  │ — see "MCP Protocol" note below
┌─────────────────────────────────▼───────────────────────────────────────────┐
│                    MCP SERVER (Cloud Run)                                    │
│                    FastAPI — apps/mcp-server/server.py                       │
│                                                                              │
│  Real MCP tools at /mcp (mcp.streamable_http_app()), each also reachable    │
│  as a plain REST route below (Pydantic input validation, JSON output):       │
│  ┌────────────────┐ ┌─────────────┐ ┌─────────────┐ ┌────────────┐ ┌──────────────┐ │
│  │/search          │ │/quote       │ │/series      │ │/indicators │ │/events       │ │
│  │search_symbols   │ │latest_quote │ │price_series │ │indicators  │ │detect_events │ │
│  │(yfinance)       │ │(yfinance)   │ │(yfinance)   │ │(pandas)    │ │(pandas)      │ │
│  └────────────────┘ └─────────────┘ └─────────────┘ └────────────┘ └──────────────┘ │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │/explain  → calls the LLM (via LiteLLM Proxy) → returns analysis text │    │
│  │  (REST only — no MCP tool; the LLM layer sits above MCP)             │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘

                    Supporting services (horizontal):

  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  ┌──────────────┐
  │   Firestore  │  │    Redis     │  │   Langfuse   │  │  Secret Manager  │  │ LiteLLM Proxy│
  │  (job state) │  │(cache+rate)  │  │ (LLM traces) │  │  (API keys)      │  │(routes every │
  │              │  │              │  │              │  │                  │  │ LLM call;    │
  │              │  │              │  │              │  │                  │  │ reports to   │
  │              │  │              │  │              │  │                  │  │ Langfuse     │
  │              │  │              │  │              │  │                  │  │ natively)    │
  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────────┘  └──────────────┘

  ┌──────────────────────────────────────────────────────────────────────────┐
  │  Cloud Logging + Cloud Trace + Cloud Monitoring (structured JSON logs)   │
  │  Filter: jsonPayload.trace_id = "abc-123"  →  full request journey       │
  └──────────────────────────────────────────────────────────────────────────┘
```

**Recent additions to this diagram, worth calling out explicitly:**

- **MCP is now real.** `apps/mcp-server/server.py` used to build a `FastMCP` object and decorate tools on it, but never actually serve it — nothing called `.run()` or mounted it, so all "MCP" traffic was really just plain REST. It's now mounted as a genuine MCP Streamable HTTP endpoint at `/mcp`, and `apps/agent-runtime`'s `MCPClient` speaks real MCP JSON-RPC to it instead of hand-rolled REST calls.
- **LLM provider is a three-way switch, not Gemini-only.** `LLM_PROVIDER` accepts `openai`, `google_ai_studio`, or `vertex_ai` (see `packages/shared-config/config.py`), each with its own fast/main/strong model tier.
- **Provider switching is per-request, not just per-deployment.** `get_llm(tier, provider=, api_key=)` lets one call use a different vendor and key than `LLM_PROVIDER`/`.env`, without mutating process state (the key is passed straight to the LLM constructor, never through `os.environ`). Lab 1's Streamlit sidebar and Lab 3/4's frontend both expose this: `GET /providers` (job-api) reports which vendors are configured server-side, and `POST /jobs`'s optional `provider`/`api_key` fields carry a student's own choice for a single run — bypassing the LiteLLM proxy for that run, since the proxy's `config.yaml` only knows the server's own keys. The key travels on the transient `PubSubMessage` envelope only; it's never written to Firestore or echoed back by `GET /jobs/{id}` (see `packages/shared-models/models.py`).
- **LiteLLM Proxy** sits in front of every LLM call in Lab 3 (Docker Compose) and Lab 4 (Cloud Run), unconditionally — including Vertex AI, not just the API-key providers. It exposes an OpenAI-compatible endpoint regardless of the backing vendor, applies basic per-key request/budget limits (`docker/litellm/config.yaml`), and reports every call to Langfuse natively via `success_callback: ["langfuse"]` — no application code involved. Vertex AI routes with no `api_key` at all: `vertex-*` model_list entries fall back to Application Default Credentials — a service account mounted from the host's `gcloud auth application-default login` session in Lab 3, or litellm-proxy's own attached IAM service account (`litellm-proxy@...`, `roles/aiplatform.user`) in Lab 4 — the same no-key-in-the-container property Workload Identity gives mcp-server/agent-runtime's own direct Vertex AI calls. Labs 1-2 (no Docker) call providers directly, since no proxy container is running there. A request carrying its own `provider`/`api_key` also skips it, since the proxy's config.yaml only knows the server's own credentials.
- **A fourth guardrail check.** Alongside the existing input/tool/output pipeline, `OutputGuardrails` now also runs an evidence-grounding check (`packages/shared-guardrails/grounding.py`): every numeric figure in the final report is verified against the tool output the crew actually received during the run (collected via `packages/shared-observability/tool_tracer.py`, which listens on CrewAI's own event bus — transport-agnostic, so it works the same whether tools were called in-process, over HTTP, or over MCP). It reports, it doesn't block.
- **CrewAI's own output guardrail, wired up for contrast.** The report-writing task also carries CrewAI's built-in `HallucinationGuardrail` (`crewai.tasks.hallucination_guardrail`). In the open-source `crewai` package it's a placeholder — `__call__` always returns "valid" and logs a link to the hosted platform, which only it implements for real. It's attached anyway so the gap is visible rather than hidden: the run result's `crewai_guardrail` field reports whether it's actually enforcing (detected, not hardcoded, via a module-level sentinel), and the deterministic grounding check above is the one actually catching invented figures.

---

## Data Flow for One Request

```
1. User types "Analyse AAPL" in Streamlit
2. Frontend: POST /jobs → {job_id: "uuid-123", status: "PENDING"}
3. Job API: validates input (guardrails) → saves to Firestore → publishes to Pub/Sub
4. Frontend: starts polling GET /jobs/uuid-123 every 2 seconds
5. Agent Worker: pulls message from Pub/Sub → checks Redis cache (miss)
6. Worker: updates Firestore → status: RUNNING
7. Research Agent: calls search_symbols("AAPL") → MCP server → yfinance → returns data
8. Research Agent: calls latest_quote("AAPL") → MCP server → yfinance
9. Research Agent: calls price_series("AAPL") → MCP server → yfinance
10. Technical Agent: calls indicators("AAPL"), detect_events("AAPL"), explain("AAPL")
11. Sector Agent: searches for AAPL peers, compares indicators
12. Report Agent: synthesizes all data → Gemini Pro → final report
13. Worker: runs Layer 3 guardrails → adds disclaimer → caches result in Redis
14. Worker: updates Firestore → status: COMPLETED, result: "..."
15. Frontend: polls GET /jobs/uuid-123 → gets COMPLETED with result
16. Frontend: renders report + tool trace + guardrail events + Langfuse link
```

---

## Service Responsibilities

| Service | Does | Doesn't Do |
|---|---|---|
| **Frontend** | Submit jobs, poll status, render results | LLM calls, tool execution |
| **Job API** | Validate, persist, publish, rate-limit | Agent logic, LLM calls |
| **Agent Runtime** | Orchestrate CrewAI crew, call MCP server (real MCP protocol) | Serve HTTP to users |
| **MCP Server** | Provide data tools over MCP + REST, call the LLM for /explain | Agent orchestration |
| **LiteLLM Proxy** | Route every LLM call, apply basic request limits, trace to Langfuse | Agent logic, tool execution |

---

## GCP Service Mapping

| Platform Need | GCP Service | Why |
|---|---|---|
| Container registry | Artifact Registry | Integrated with GKE + Cloud Run |
| Frontend + APIs | Cloud Run | Serverless, scales to 0, easy HTTPS |
| Agent workers | GKE Autopilot | Long-running, scalable, Workload Identity |
| Message queue | Pub/Sub | Managed, at-least-once, dead-letter built-in |
| Job state | Firestore | Serverless, real-time listeners, no schema |
| Cache + rate limit | Memorystore (Redis) | Managed Redis, <1ms latency |
| LLM provider | Vertex AI (Gemini), or OpenAI/Google AI Studio via LiteLLM Proxy | Vertex AI needs no API key with Workload Identity; LiteLLM Proxy adds tracing + basic limits for the API-key path |
| Secrets | Secret Manager | Never in code, versioned, audit logged |
| Logs | Cloud Logging | Searchable by trace_id, JSON-native |
| Traces | Cloud Trace | Distributed trace timeline |
| CI/CD | Cloud Build / GitHub Actions | Push-to-deploy |

---

## Cost Breakdown (Approximate, ~10 requests/day teaching scenario)

| Service | Cost/month |
|---|---|
| Cloud Run (frontend + APIs) | ~$0 (free tier covers 2M req/month) |
| GKE Autopilot (1 agent pod, idle) | ~$30 |
| Memorystore Redis (1GB) | ~$25 |
| Firestore | ~$0 (free tier: 1GB + 50K reads/day) |
| Pub/Sub | ~$0 (free tier: 10GB/month) |
| Gemini (100 requests/month) | ~$1–5 |
| **Total** | **~$55–60/month** |

**Save money when not teaching:** `make infra-down` destroys GKE + Redis + Cloud SQL → $0/day idle.
