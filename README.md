# Stock Agent Platform — Production Architecture

> **A teaching project** showing how to evolve a CrewAI + MCP stock analysis demo into a production-ready platform on Google Cloud Platform.

---

## 🎯 What This Project Teaches

| Concept | Where to find it |
|---|---|
| Agent architecture (CrewAI) | `apps/agent-runtime/worker.py` |
| MCP server as a service | `apps/mcp-server/server.py` |
| Async job processing | `apps/job-api/main.py` → `docker/docker-compose.yml` |
| 3-layer guardrails | `packages/shared-guardrails/guardrails.py` |
| Structured logging + tracing | `packages/shared-observability/observability.py` |
| LLM cost routing | `packages/shared-config/config.py` → `get_llm()` |
| GCP infrastructure as code | `infra/terraform/main.tf` |
| Kubernetes autoscaling | `infra/kubernetes/deployment.yaml` |
| CI/CD pipeline | `.github/workflows/ci-cd.yml` |

---

## 🏗️ 4-Stage Evolution

```
Stage 1: Local Demo          Stage 2: Containers         Stage 3: Cloud-Ready         Stage 4: Production
─────────────────────────    ────────────────────────    ─────────────────────────    ────────────────────
One Python script            docker-compose.yml          Cloud Run (3 services)       GKE Autopilot workers
Everything in-process        Network boundaries          Secret Manager               Pub/Sub async queue
No observability             Redis + Pub/Sub emulator    Cloud Logging                HPA autoscaling
OpenAI API key in .env       Langfuse (local)            Cloud Trace                  Workload Identity
                                                         Firestore                    Langfuse (cloud)
```

---

## 🚀 Quick Start (Local — Stage 2)

### Prerequisites
- Docker Desktop
- A free [Google AI Studio](https://aistudio.google.com/apikey) API key

```bash
# 1. Clone
git clone https://github.com/zviba/mcp_stocks_demo_crewai_exercise
cd mcp_stocks_demo_crewai_exercise

# 2. Configure
cp .env.example .env
# Edit .env: set GEMINI_API_KEY=your-key-here

# 3. Start everything
make up

# 4. Open the UI
open http://localhost:8501
```

**What starts:**
| Service | URL | Description |
|---|---|---|
| Streamlit UI | http://localhost:8501 | User-facing app |
| Job API | http://localhost:8000/docs | Async job gateway |
| MCP Server | http://localhost:8001/docs | Tool/data server |
| Langfuse | http://localhost:3000 | LLM call traces |
| Redis | localhost:6379 | Cache + rate limiting |
| Pub/Sub emulator | localhost:8085 | Async message queue |

**Langfuse login:** `admin@localhost` / `admin123`

---

## ☁️ Deploy to GCP (Stage 3/4)

### Prerequisites
- GCP project with billing enabled
- `gcloud`, `terraform`, `docker` installed
- Authenticated: `gcloud auth login`

```bash
# One-command setup (provisions infra + deploys services)
./scripts/setup-gcp.sh --project=your-gcp-project-id

# Or step by step:
make infra-up GCP_PROJECT=your-project    # Terraform: GKE, Redis, Pub/Sub, Firestore
make build                                # Build Docker images
make push GCP_PROJECT=your-project        # Push to Artifact Registry
make deploy-run GCP_PROJECT=your-project  # Deploy to Cloud Run
```

**Cost when idle:** ~$0/day (Cloud Run scales to 0, Firestore/Pub/Sub are pay-per-use)

**Stop billing when not teaching:**
```bash
make infra-down  # Destroys GKE + Redis + Cloud SQL (keeps Firestore/Pub/Sub)
```

---

## 📂 Repository Structure

```
├── apps/
│   ├── frontend-streamlit/    # Streamlit UI (polls Job API)
│   ├── job-api/               # FastAPI: accept → persist → publish → return
│   ├── agent-runtime/         # CrewAI workers consuming Pub/Sub
│   └── mcp-server/            # Tool/data service (yfinance + Gemini)
│
├── packages/
│   ├── shared-models/         # Pydantic models (contract between services)
│   ├── shared-config/         # Settings + LLM factory (model routing)
│   ├── shared-observability/  # Structured logging + Langfuse + Cloud Trace
│   └── shared-guardrails/     # 3-layer safety system
│
├── infra/
│   ├── terraform/             # GCP infrastructure (GKE, Redis, Pub/Sub...)
│   └── kubernetes/            # K8s manifests (Deployment + HPA)
│
├── docker/
│   └── docker-compose.yml     # Full local stack
│
├── tests/
│   └── unit/                  # pytest unit tests (guardrails, models)
│
├── scripts/
│   └── setup-gcp.sh           # One-command GCP deployment
│
├── .github/workflows/
│   └── ci-cd.yml              # GitHub Actions CI/CD
│
├── docs/architecture/         # Architecture diagrams and explanations
├── .env.example               # Environment variable template
└── Makefile                   # All commands in one place
```

---

## 🔑 Key Design Decisions

### 1. Gemini Model Routing (Cost Control)
```
FAST  → gemini-2.5-flash-lite   → Guardrail checks, intent classification (~free)
MAIN  → gemini-2.5-flash        → All 4 agent tasks (research, technical, sector)
STRONG→ gemini-2.5-pro          → Final report synthesis (used sparingly)
```

### 2. Async Job Pattern
```
POST /jobs → 202 Accepted → {job_id}    (returns in <100ms)
GET  /jobs/{job_id} → poll until COMPLETED
```
vs. demo: `streamlit blocks for 2+ minutes`

### 3. Three-Layer Guardrails
- **Input**: length, injection patterns, intent check
- **Tool**: allowlist, argument validation, call count limit
- **Output**: secret redaction, prediction flagging, financial disclaimer

### 4. Workload Identity (No credentials in containers)
```
GKE pod → [Workload Identity] → GCP Service Account → Vertex AI / Firestore / Pub/Sub
No GEMINI_API_KEY in the container. No credentials to rotate or leak.
```

---

## 🛠️ Development Commands

```bash
make help          # Show all commands
make up            # Start local stack
make down          # Stop local stack
make logs          # Tail all service logs
make test          # Run unit tests
make lint          # Lint with ruff
make format        # Format with ruff

make infra-plan    # Preview Terraform changes
make infra-up      # Provision GCP infrastructure
make deploy-run    # Deploy to Cloud Run
make deploy-gke    # Deploy workers to GKE
make scale-workers REPLICAS=5  # Scale agent pods
```

---

## 📊 Observability

### Local
- **Langfuse**: http://localhost:3000 — see every Gemini call, prompt, token count
- **Docker logs**: `make logs` — structured JSON logs with trace_id correlation

### Production (GCP)
- **Cloud Logging**: Filter by `jsonPayload.trace_id="<id>"` to trace one request
- **Cloud Trace**: Timeline view across all services
- **Cloud Monitoring**: Dashboards for queue depth, error rate, latency, token usage

---

## 🔒 Security Notes

- Secrets live in **Secret Manager**, never in code or Docker images
- Each service has a **least-privilege service account** (4 separate SAs)
- **Internal API token** protects service-to-service calls
- **Workload Identity** eliminates long-lived credential files in containers
- All logs are **sanitized** — no API keys or tokens in output

---

## 📚 Related Resources

- [CrewAI Documentation](https://docs.crewai.com)
- [Google Gemini on Vertex AI](https://cloud.google.com/vertex-ai/generative-ai/docs/model-reference/gemini)
- [Cloud Pub/Sub vs Kafka](docs/architecture/messaging-comparison.md)
- [Langfuse Self-Hosted](https://langfuse.com/docs/deployment/self-host)
- [GKE Autopilot](https://cloud.google.com/kubernetes-engine/docs/concepts/autopilot-overview)
- [Original Demo Repo](https://github.com/zviba/mcp_stocks_demo_crewai_exercise)
