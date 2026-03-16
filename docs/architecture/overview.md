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
│  │  Research Agent ──────────┐  (gemini-2.5-flash)                     │    │
│  │    search_symbols          │                                          │    │
│  │    latest_quote           ▼                                          │    │
│  │    price_series    Technical Agent ───┐  (gemini-2.5-flash)         │    │
│  │                      indicators       │                               │    │
│  │                      detect_events   ▼                               │    │
│  │                      explain  Sector Agent ───┐  (gemini-2.5-flash) │    │
│  │                               compare peers  ▼                      │    │
│  │                                       Report Agent  (gemini-2.5-pro)│    │
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
                                  │ HTTP POST (tool calls)
┌─────────────────────────────────▼───────────────────────────────────────────┐
│                    MCP SERVER (Cloud Run)                                    │
│                    FastAPI — apps/mcp-server/server.py                       │
│                                                                              │
│  Tools (each with Pydantic input validation + structured JSON output):       │
│  ┌────────────┐ ┌─────────────┐ ┌─────────────┐ ┌────────────┐ ┌────────┐ │
│  │/search     │ │/quote       │ │/series      │ │/indicators │ │/events │ │
│  │(yfinance)  │ │(yfinance)   │ │(yfinance)   │ │(pandas)    │ │(pandas)│ │
│  └────────────┘ └─────────────┘ └─────────────┘ └────────────┘ └────────┘ │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │/explain  → calls Gemini (gemini-2.5-flash) → returns analysis text  │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘

                    Supporting services (horizontal):

  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐
  │   Firestore  │  │    Redis     │  │   Langfuse   │  │  Secret Manager  │
  │  (job state) │  │(cache+rate)  │  │ (LLM traces) │  │  (API keys)      │
  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────────┘

  ┌──────────────────────────────────────────────────────────────────────────┐
  │  Cloud Logging + Cloud Trace + Cloud Monitoring (structured JSON logs)   │
  │  Filter: jsonPayload.trace_id = "abc-123"  →  full request journey       │
  └──────────────────────────────────────────────────────────────────────────┘
```

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
| **Agent Runtime** | Orchestrate CrewAI crew, call MCP server | Serve HTTP to users |
| **MCP Server** | Provide data tools, call Gemini for /explain | Agent orchestration |

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
| LLM provider | Vertex AI (Gemini) | No API key needed with Workload Identity |
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
