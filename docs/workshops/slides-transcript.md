# Workshop Slide Transcript
## "From Local Demo to Cloud Platform — Building Production AI Agents"
### ~40 Slides · 3.5 Hours

---

---

## SLIDE 0 — Before We Start: Get Your Free GCP Credits

**Title:** 🎁 Free Tier — Everything You Need is Free Today

**Content:**
- **Google Cloud Free Tier:** 90-day trial + **$300 in credits**
  - Enough to run this entire workshop end-to-end
  - GKE Autopilot, Cloud Run, Redis, Firestore — all covered
- **Step 1:** Go to → `https://cloud.google.com/free`
  - Sign in with a Google account
  - Set up billing (won't be charged during free tier)
- **Step 2:** Get a free Gemini API Key (Google AI Studio)
  - Go to → `https://aistudio.google.com/apikey`
  - Click "Create API Key" — takes 30 seconds
  - This is the key you'll put in your `.env` file
- **Step 3:** Install the tools (if not done already):
  ```
  brew install google-cloud-sdk terraform
  docker --version  ← make sure Docker Desktop is running
  ```

**Speaker Note:** *Ask the room — who already has a GCP account? Who has Gemini API key? Give 5 minutes for anyone who needs to set this up now.*

---

---

## SLIDE 1 — Workshop Overview

**Title:** 🗺️ From Local Demo to Cloud Platform

**Content:**

| Time | Section |
|------|---------|
| 0:00 – 0:20 | Intro + Project Overview |
| 0:20 – 1:00 | Lab 1 — Sync Single Process |
| 1:00 – 1:40 | Lab 2 — Async HTTP Services |
| 1:40 – 2:00 | ☕ Break |
| 2:00 – 2:40 | Lab 3 — Pub/Sub + Docker Compose |
| 2:40 – 3:00 | 🚀 Going to the Cloud |
| 3:00 – 3:30 | Lab 4 — Full GCP Production |
| 3:30 – 3:50 | Summary + Q&A |

**What you'll build today:**
- A stock analysis platform powered by AI agents
- Starting from a single Python script
- Ending with a fully deployed, auto-scaling cloud system

**Speaker Note:** *Today is a journey. Same business logic — but how it runs changes completely across 4 stages.*

---

## SLIDE 2 — About the Speaker

**Title:** 👋 Hi, I'm Orel

**Content:**

**Orel Lavie**
- 🎓 M.Sc. — Machine Learning
- 🏢 ML Researcher @ **LSports**
  - Real-time sports data & analytics platform
  - Working with live data pipelines, ML models, and AI agents at scale
- 💻 Building production ML systems:
  - Real-time prediction engines
  - Data pipelines processing millions of events/day
  - AI agents for sports analytics

**Why this workshop?**
> "I kept seeing teams build brilliant ML prototypes that collapsed under real-world load. This workshop shows you the *exact path* from prototype to production."

**Repo:** `github.com/zviba/mcp_stocks_demo_crewai_exercise`

---

## SLIDE 3 — The Project: What Are We Building?

**Title:** 📈 Stock Analysis Agent Platform

**Content:**

**The Business Problem:**
- Users want a deep AI-powered analysis of any stock ticker
- Research agent + Technical analysis + Sector comparison + Full report
- Powered by: **CrewAI** (agent orchestration) + **Gemini** (LLM) + **yfinance** (data)

**The 4 Agents:**
| Agent | Job | LLM Model |
|-------|-----|-----------|
| 🔍 Research Agent | Find company info & news | gemini-2.5-flash |
| 📊 Technical Agent | RSI, SMA, EMA indicators | gemini-2.5-flash |
| 🏭 Sector Agent | Compare vs. peers (MSFT, NVDA…) | gemini-2.5-flash |
| 📝 Report Writer | Synthesize → final report | gemini-2.5-pro |

**The Tools (MCP — Model Context Protocol):**
- `search` — find a stock symbol by name
- `quote` — get latest price, change, volume
- `indicators` — calculate RSI, SMA, EMA
- `events` — detect gaps, spikes, 52-week highs/lows
- `explain` — AI-powered narrative summary

---

## SLIDE 4 — The 4-Stage Evolution

**Title:** 🧬 One Product, Four Architectures

**Content:**

```
Lab 1           Lab 2           Lab 3              Lab 4
─────────       ─────────       ─────────          ─────────
Single script   HTTP services   Docker + Pub/Sub   Full GCP
Blocks UI       Async 202/poll  Message queue      Auto-scale
No retry        No retry        Auto-retry ×3      HPA + WI
No observ.      Basic logs      Langfuse            Cloud Trace
API key         API key         API key             Workload Identity
FREE            FREE            FREE               ~$0.50/hr idle
```

**The key insight:**
> The **core business logic is identical** across all 4 labs.
> What changes is **how the components are wired together**.

---

## SLIDE 5 — Core Technologies (Quick Reference)

**Title:** 🧰 Tech Stack at a Glance

**Content:**

| Technology | Role | Used in |
|---|---|---|
| **CrewAI** | Multi-agent orchestration | All labs |
| **Gemini** (Google AI) | LLM for all agents | All labs |
| **yfinance** | Stock market data | All labs |
| **FastAPI** | REST APIs | Labs 2–4 |
| **Streamlit** | Web UI | All labs |
| **Redis** | Rate limiting + idempotency | Labs 3–4 |
| **Pub/Sub** | Async message queue | Labs 3–4 |
| **Docker Compose** | Local container stack | Lab 3 |
| **Terraform** | Infrastructure as Code | Lab 4 |
| **GKE Autopilot** | Serverless Kubernetes | Lab 4 |
| **Cloud Run** | Serverless containers | Lab 4 |
| **Langfuse** | LLM observability | Labs 3–4 |

---

---

# ── LAB 1 ──────────────────────────────────────────────────────────────

## SLIDE 6 — Lab 1: Setup & Run

**Title:** 🛠️ Lab 1 — Setup in 3 Commands

**Content:**

**Prerequisites:**
```bash
git clone https://github.com/zviba/mcp_stocks_demo_crewai_exercise.git
cd mcp_stocks_demo_crewai_exercise
cp .env.example .env
# Edit .env → set GEMINI_API_KEY=your-key-here
```

**Terminal 1 — Health check API:**
```bash
pip install -r requirements.txt
uvicorn api:app --host 127.0.0.1 --port 8001
```

**Terminal 2 — Streamlit UI:**
```bash
streamlit run streamlit_crewai_app.py
```

**Open:** `http://localhost:8501`
- Enter your Gemini API key in the sidebar
- Type `AAPL` → click **Start Analysis**
- ⏳ Wait 1–2 minutes…

**Exercise:** Open a second browser tab → try submitting `MSFT`. What happens?

---

## SLIDE 7 — Lab 1: Architecture

**Title:** 🏗️ Lab 1 — Single Process, Everything In-Memory

**Content:**

```
Browser
   │
   ▼
streamlit_crewai_app.py   ← UI + business logic (SAME process!)
   │  direct Python import
   ▼
agents.py                 ← 4 CrewAI agents, sequential crew
   │  direct Python import
   ▼
mcp_server.py             ← @tool functions (not an actual server!)
   │  direct Python import
   ▼
datasource.py             ← yfinance → Yahoo Finance API
```

**Key files:**
| File | Role |
|------|------|
| `streamlit_crewai_app.py` | UI + calls `run_crewai_analysis()` |
| `agents.py` | Agent + task definitions |
| `mcp_server.py` | `@tool`-decorated functions |
| `datasource.py` | yfinance wrapper |

---

## SLIDE 8 — Lab 1: What Works Well ✅

**Title:** ✅ Lab 1 — Functional Strengths

**Content:**

**Functional Requirements — ALL MET:**
- ✅ Takes a stock ticker as input
- ✅ Runs 4 specialized AI agents in sequence
- ✅ Calls real market data tools (yfinance)
- ✅ Produces a structured analysis report with disclaimer
- ✅ Works completely offline (no cloud needed)
- ✅ Zero infra setup — just `pip install` and run

**Non-Functional Strengths:**
- ✅ **Zero latency to start** — no Docker, no containers, no boot time
- ✅ **Debuggable** — set a breakpoint anywhere, single Python process
- ✅ **Readable** — all logic in 4 files, < 500 lines total
- ✅ **Portable** — runs on any machine with Python 3.11+
- ✅ **Free** — no cloud costs, no Docker, just a Gemini API key

**This is perfect for:**
- Prototyping a new agent idea
- Demos to stakeholders
- Learning CrewAI and MCP concepts

---

## SLIDE 9 — Lab 1: What Breaks at Scale ❌

**Title:** ❌ Lab 1 — The Walls You'll Hit

**Content:**

**The Demo Runs Fine. Then Someone Else Tries to Use It.**

| Problem | What happens | Root cause |
|---------|-------------|------------|
| 🔴 **Blocking UI** | Browser spins for 2 min | `streamlit.run()` is synchronous — blocks the entire process |
| 🔴 **Single user only** | 2nd tab waits for 1st to finish | 1 Python process = 1 thread of execution |
| 🔴 **Browser close = lost job** | Analysis disappears | No persistent job state — lives in Streamlit session |
| 🔴 **API key in UI** | Leaked on refresh, shared screens | Key stored in `st.session_state` — visible in HTML |
| 🔴 **No observability** | "Did it actually call the tools?" | No logs, no traces, no tool call durations |
| 🔴 **No retry** | Failure = re-run everything manually | No error handling, no queue |

**The fundamental problem:**
> One Python process doing everything. No separation of concerns.
> This works for a demo. It **cannot** serve real users.

**→ What if we separate the UI from the compute?**

---

---

# ── LAB 2 ──────────────────────────────────────────────────────────────

## SLIDE 10 — Lab 2: Setup & Run

**Title:** 🛠️ Lab 2 — 4 Terminals, 4 Services

**Content:**

**Install once:**
```bash
pip install -r apps/mcp-server/requirements.txt
pip install -r apps/job-api/requirements.txt
pip install -r apps/agent-runtime/requirements.txt
pip install -r apps/frontend-streamlit/requirements.txt
```

**Terminal 1 — MCP Server (port 8001):**
```bash
cd apps/mcp-server
GEMINI_API_KEY=your-key LOG_FORMAT=text \
uvicorn server:app --host 0.0.0.0 --port 8001
```

**Terminal 2 — Agent Runtime (port 8002):**
```bash
cd apps/agent-runtime
GEMINI_API_KEY=your-key MCP_SERVER_URL=http://localhost:8001 \
JOB_API_URL=http://localhost:8000 LOG_FORMAT=text \
python worker.py --mode http
```

**Terminal 3 — Job API (port 8000):**
```bash
cd apps/job-api
GEMINI_API_KEY=your-key AGENT_RUNTIME_URL=http://localhost:8002 \
LOG_FORMAT=text uvicorn main:app --host 0.0.0.0 --port 8000
```

**Terminal 4 — Frontend (port 8501):**
```bash
JOB_API_URL=http://localhost:8000 \
streamlit run apps/frontend-streamlit/app.py
```

---

## SLIDE 11 — Lab 2: Architecture

**Title:** 🏗️ Lab 2 — Service Boundaries, Async Pattern

**Content:**

```
Browser
   │  POST /jobs → 202 Accepted → {job_id}
   │  GET  /jobs/{job_id}  ← polls every 2 seconds
   ▼
apps/job-api/main.py  :8000
  (validate → persist PENDING → dispatch to worker → return 202)
   │  asyncio.create_task → POST /analyze (background)
   ▼
apps/agent-runtime/worker.py  :8002  (--mode http)
  (runs CrewAI crew → PATCH /jobs/{job_id} with result)
   │  POST /quote  /indicators  /events…
   ▼
apps/mcp-server/server.py  :8001
  (production MCP server: auth + logging + health checks)
   │
   ▼
datasource.py → yfinance → Yahoo Finance API
```

**The key shift:**
> `HTTP 202 Accepted` = "I received your request. It's being processed."
> The browser **does not wait**. It polls.

---

## SLIDE 12 — Lab 2: Functional & Non-Functional Gains ✅

**Title:** ✅ Lab 2 — What We Fixed

**Content:**

**Functional Gains:**
- ✅ **Async submission** — UI returns in < 100ms with a `job_id`
- ✅ **Concurrent jobs** — submit AAPL and MSFT simultaneously, both run
- ✅ **Job survives browser close** — state lives in job-api's in-memory store
- ✅ **Guardrails at the API** — `"Ignore previous instructions"` → 400 blocked
- ✅ **Input validation** — Pydantic schemas on every endpoint
- ✅ **Auth between services** — `X-Internal-Token` header on internal calls

**Non-Functional Gains:**
- ✅ **Observable** — structured JSON logs in every service with `trace_id`
- ✅ **Debuggable** — each service runs independently, restartable
- ✅ **API-documented** — FastAPI auto-generates Swagger at `/docs`
- ✅ **Separation of concerns** — MCP server doesn't know about agents; agents don't know about UI

**New concepts introduced:**
- `HTTP 202 Accepted` — the async pattern
- Shared models (`packages/shared-models`) — contract between services
- 3-layer guardrails: Input → Tool → Output

---

## SLIDE 13 — Lab 2: Still Broken ❌

**Title:** ❌ Lab 2 — The Remaining Problems

**Content:**

| Problem | What happens | Root cause |
|---------|-------------|------------|
| 🔴 **No retry** | Worker crash = job lost forever | HTTP is fire-and-forget; no queue |
| 🔴 **Tight coupling** | Job API must know agent-runtime's URL | Direct HTTP dependency |
| 🔴 **No job persistence** | Restart job-api → all in-memory jobs lost | `dict` in memory |
| 🔴 **No result caching** | Same AAPL query twice = 2 full LLM runs | No Redis, no shared cache |
| 🔴 **No LLM visibility** | How many tokens? Which call was slowest? | No Langfuse / tracing |
| 🔴 **Manual scaling** | Want 3 workers? Manually launch 3 terminals | No orchestration |
| 🟡 **Env vars manually set** | Copy-paste 10 env vars per terminal | No `.env` injection |

**The key question:**
> What if the agent-runtime crashes halfway through a job?
> In Lab 2: **the job is gone.** The user sees "RUNNING" forever.

**→ We need a durable message queue. Enter Pub/Sub.**

---

---

# ── LAB 3 ──────────────────────────────────────────────────────────────

## SLIDE 14 — Lab 3: Setup & Run

**Title:** 🛠️ Lab 3 — One Command, Everything Starts

**Content:**

**Prerequisites:** Docker Desktop running + `.env` has `GEMINI_API_KEY`

```bash
# Start the full stack (builds images first time ~5 min)
make up

# OR directly:
docker compose -f docker/docker-compose.yml up --build -d

# Watch the startup sequence:
make logs
```

**Watch the startup order in the logs:**
```
1. redis         → healthy ✅
2. pubsub-emul.  → healthy ✅
3. pubsub-init   → creates topics → exits ✅
4. langfuse-pg   → healthy ✅
5. langfuse      → healthy ✅
6. mcp-server    → healthy ✅
7. job-api       → healthy ✅
8. agent-runtime → starts  ✅
9. frontend      → healthy ✅
```

**Access points:**
| Service | URL |
|---------|-----|
| 🖥️ Streamlit UI | `http://localhost:8501` |
| 📋 Job API docs | `http://localhost:8000/docs` |
| 🔧 MCP Server docs | `http://localhost:8001/docs` |
| 🔍 Langfuse UI | `http://localhost:3000` (admin@localhost / admin123) |

**Cleanup:** `make down` (keeps volumes) or `make clean` (removes everything)

---

## SLIDE 15 — Lab 3: Architecture

**Title:** 🏗️ Lab 3 — Full Local Stack with Message Queue

**Content:**

```
Browser :8501
   │  POST /jobs
   ▼
job-api :8000
  (Firestore in-memory, Redis rate limiting + idempotency)
   │  publish message
   ▼
Pub/Sub Emulator :8085
  (GCP Pub/Sub running locally — durable queue)
   │  deliver message
   ▼
agent-runtime (Pub/Sub subscriber mode)
  (subscribes to topic, processes job, acks or nacks)
   │  POST /search /quote /indicators…
   ▼
mcp-server :8001
   │
   ▼
Redis :6379 ← result cache (TTL 1h)

Langfuse :3000 ← all LLM traces, token counts, costs
```

**9 containers. Zero cloud. Fully local.**

---

## SLIDE 16 — Lab 3: Docker Compose Stack

**Title:** 📦 Lab 3 — The 9-Container Stack

**Content:**

| Container | Port | Purpose |
|-----------|------|---------|
| `redis` | 6379 | Cache + rate limiting |
| `pubsub-emulator` | 8085 | GCP Pub/Sub locally |
| `pubsub-init` | — | Creates topics (runs once) |
| `langfuse-postgres` | — | Langfuse's database |
| `langfuse` | 3000 | LLM observability UI |
| `mcp-server` | 8001 | Stock data tools |
| `job-api` | 8000 | Async job gateway |
| `agent-runtime` | 8002 | CrewAI workers |
| `frontend-streamlit` | 8501 | Web UI |

**Scale workers instantly:**
```bash
docker compose -f docker/docker-compose.yml up --scale agent-runtime=3 -d
# → 3 workers competing for Pub/Sub messages
# → Submit 5 jobs → distributed across all 3
```

---

## SLIDE 17 — Lab 3: Functional & Non-Functional Gains ✅

**Title:** ✅ Lab 3 — What We Fixed

**Content:**

**Functional Gains:**
- ✅ **Auto-retry** — Pub/Sub redelivers on failure, up to 3 times
- ✅ **Dead-letter queue** — Failed jobs go to `analysis-dlq` for inspection
- ✅ **Worker crash safe** — Message is redelivered to another worker
- ✅ **Result caching** — Same AAPL query twice? Second is instant (0 LLM calls)
- ✅ **Rate limiting** — Redis: max N requests/minute per user ID
- ✅ **Idempotency** — Same request + same key = same job ID returned
- ✅ **Loose coupling** — Job API publishes a message; any worker picks it up

**Non-Functional Gains:**
- ✅ **LLM observability** — Langfuse shows every prompt, response, tokens, cost
- ✅ **Distributed tracing** — Same `trace_id` visible across all service logs
- ✅ **Container isolation** — Each service in its own container
- ✅ **Health checks** — Docker restarts unhealthy containers automatically
- ✅ **Horizontal scaling** — `--scale agent-runtime=N` with one command

**New concepts introduced:**
- Pub/Sub vs HTTP coupling
- Redis caching patterns (`analysis:{hash}`)
- Langfuse for LLM cost visibility
- Docker health checks + dependency ordering

---

## SLIDE 18 — Lab 3: The Pub/Sub Advantage

**Title:** 📬 Why Pub/Sub > Direct HTTP

**Content:**

| Aspect | Lab 2 (HTTP) | Lab 3 (Pub/Sub) |
|--------|-------------|-----------------|
| Retry on failure | ❌ Manual | ✅ Automatic (3 attempts) |
| Dead letter queue | ❌ None | ✅ `analysis-dlq` topic |
| Worker crash | Job lost permanently | Message redelivered automatically |
| Scale workers | Restart required | `--scale agent-runtime=N` |
| Worker language | Must be Python | Any language with Pub/Sub SDK |
| Job ordering | Depends on timing | Guaranteed FIFO within subscriber |

**The mental model:**
```
HTTP:    "Hey worker, do this NOW." (worker might be dead)
Pub/Sub: "I'm leaving a note in the queue." (any worker picks it up, eventually)
```

**This is how every major system works at scale:**
> Slack, Uber, Netflix, Google — all use durable queues for async work.

---

## SLIDE 19 — Lab 3: Redis Caching Deep-Dive

**Title:** ⚡ Redis Caching — Same Query, Zero LLM Calls

**Content:**

**How it works:**
```python
# In agent-runtime/worker.py:
cache_key = f"analysis:{hash(query)}"

# Check Redis first
cached = redis.get(cache_key)
if cached:
    return cached  # ← returns in milliseconds, 0 LLM calls!

# Not cached → run the crew (~9 minutes)
result = crew.kickoff(inputs={"query": query})

# Cache the result (TTL = 1 hour)
redis.setex(cache_key, 3600, result)
```

**Try it yourself:**
```bash
# Submit "Analyse AAPL" → wait for completion
# Submit "Analyse AAPL" again → notice it returns in < 100ms

# Check what's in Redis:
docker exec -it stock-agent-redis redis-cli keys "analysis:*"
```

**Important note on what Redis is NOT used for:**
> Redis does NOT cache individual stock data (yfinance calls).
> It caches the **full analysis result**. The ~9 min latency is from
> Gemini LLM inference (~534s), not from data fetching (~16s).

---

## SLIDE 20 — Lab 3: Still Broken ❌

**Title:** ❌ Lab 3 — The Last Remaining Problems

**Content:**

| Problem | What happens | Root cause |
|---------|-------------|------------|
| 🔴 **Not the real cloud** | Pub/Sub is an emulator | If GCP emulator differs → bugs in production |
| 🔴 **API keys in .env** | Anyone with `.env` has full Gemini access | No secrets management |
| 🔴 **No auto-scaling** | You manually set `--scale N` | No demand-based autoscaling |
| 🔴 **Not accessible publicly** | `localhost` only | No HTTPS, no domain, no CDN |
| 🔴 **Dev laptop dependency** | "Works on my machine" | No CI/CD, no reproducible builds |
| 🔴 **Docker on laptop** | 9 containers on your MacBook | Limited RAM, battery, uptime |
| 🟡 **Local Langfuse** | Lost when `make clean` is run | No persistent cloud storage |

**The remaining gap:**
> Lab 3 is a **great local development environment**.
> But it's not what real users interact with.
> Time to deploy to the real cloud.

---

---

# ── TRANSITION ─────────────────────────────────────────────────────────

## SLIDE 21 — 🚀 We're Going to GCP Now

**Title:** ☁️ Leaving Localhost — Moving to Real Google Cloud

**Content:**

> ### From this slide onwards, we are no longer running locally.
> Everything runs on **Google Cloud Platform**.

**What changes:**
| Local (Labs 1–3) | GCP (Lab 4) |
|---|---|
| Pub/Sub **emulator** | Real **GCP Pub/Sub** |
| Redis in Docker | **Memorystore Redis** (managed) |
| Langfuse in Docker | **Langfuse on Cloud SQL** |
| Firestore in-memory | Real **GCP Firestore** |
| Docker containers | **Cloud Run** + **GKE Autopilot** |
| API key in `.env` | **Secret Manager** + **Workload Identity** |
| `localhost:8501` | `https://your-app.run.app` |

**The same code. The same logic. Real infrastructure.**

**Cost check:**
- Running the workshop live: ~$2–5 total
- Idle overnight: ~$0.50/hr (GKE + Redis)
- After workshop: run `make infra-down` → drops to ~$0/day

---

---

# ── LAB 4 ──────────────────────────────────────────────────────────────

## SLIDE 22 — Lab 4: Setup & Run

**Title:** 🛠️ Lab 4 — One Command to Rule Them All

**Content:**

**Prerequisites:**
```bash
# Authenticate with GCP
gcloud auth login
gcloud auth application-default login

# Set your project
export GCP_PROJECT=your-project-id
export GCP_REGION=us-central1
```

**Full deployment (one command):**
```bash
make setup-gcp GCP_PROJECT=$GCP_PROJECT GCP_REGION=$GCP_REGION
```

**What this does (~10-15 min total):**
1. ✅ Enables all required GCP APIs
2. ✅ Terraform: provisions GKE, Redis, Pub/Sub, Firestore, Artifact Registry (~5 min)
3. ✅ Builds + pushes all 4 Docker images to Artifact Registry
4. ✅ Deploys MCP Server, Job API, Frontend to **Cloud Run**
5. ✅ Deploys Agent Runtime workers to **GKE Autopilot**
6. ✅ Prints all live HTTPS service URLs

**Tear down when done:**
```bash
make infra-down GCP_PROJECT=$GCP_PROJECT GCP_REGION=$GCP_REGION
# Destroys: GKE cluster, Redis, Cloud SQL (~$0.50/hr)
# Keeps: Firestore, Pub/Sub, Artifact Registry (near-free)
```

---

## SLIDE 23 — Lab 4: GCP Architecture

**Title:** 🏗️ Lab 4 — Full Production Architecture on GCP

**Content:**

```
User Browser (HTTPS)
   │
   ▼
Cloud Run: frontend-streamlit   ← scale-to-zero, global HTTPS
   │  POST /jobs
   ▼
Cloud Run: job-api              ← scale-to-zero, autoscales 0→N
   ├── Firestore: job state
   └── Memorystore Redis: rate limiting + idempotency
   │  publish
   ▼
GCP Pub/Sub (managed)           ← durable, at-least-once delivery
   │  subscribe
   ▼
GKE Autopilot: agent-runtime    ← HPA scales on Pub/Sub queue depth
   └── Workload Identity → Vertex AI / Firestore / Pub/Sub
   │  POST /quote /indicators…
   ▼
Cloud Run: mcp-server           ← scale-to-zero

Secret Manager ── secrets injected as env vars (never in code)
Artifact Registry ── Docker images (built by GitHub Actions)
Cloud Logging ── structured JSON from all services
Cloud Trace ── X-Trace-ID propagated across all hops
```

---

## SLIDE 24 — Lab 4: Infrastructure as Code (Terraform)

**Title:** 🧱 Terraform — Reproducible Infrastructure

**Content:**

**`infra/terraform/main.tf` provisions:**
```hcl
# GKE Autopilot cluster
resource "google_container_cluster" "agent_cluster" {
  name     = "agent-cluster"
  enable_autopilot = true
}

# Memorystore Redis
resource "google_redis_instance" "cache" {
  name           = "stock-agent-cache"
  memory_size_gb = 1
  authorized_network = google_compute_network.vpc.id
}

# Pub/Sub topic + subscription + dead-letter
resource "google_pubsub_topic" "agent_jobs" {
  name = "agent-jobs"
}

# Firestore database
resource "google_firestore_database" "main" {
  name = "(default)"
  type = "FIRESTORE_NATIVE"
}
```

**Why Terraform?**
- Destroy and recreate infrastructure in minutes
- Version-controlled — infra changes go through code review
- `make infra-plan` → shows what will change before applying
- `make infra-down` → destroys everything cleanly

---

## SLIDE 25 — Lab 4: Cloud Run (The Serverless Trio)

**Title:** ☁️ Cloud Run — Serverless Containers (3 Services)

**Content:**

**Three services deployed to Cloud Run:**

| Service | URL | Scales to |
|---------|-----|-----------|
| `frontend-streamlit` | `https://frontend-*.run.app` | 0 → N instances |
| `job-api` | `https://job-api-*.run.app` | 0 → N instances |
| `mcp-server` | `https://mcp-server-*.run.app` | 0 → N instances |

**Key properties of Cloud Run:**
- ✅ **Scale to zero** — no traffic = no cost (even when deployed)
- ✅ **HTTPS by default** — no nginx, no certs to manage
- ✅ **Global CDN** — auto-served from nearest region
- ✅ **Rollback in seconds** — `gcloud run deploy --revision-suffix=v2`
- ✅ **Secrets injected at runtime** — via Secret Manager, not baked into image

**VPC access for Redis:**
```bash
--network=stock-agent-vpc
--subnet=stock-agent-subnet
--vpc-egress=private-ranges-only
# Allows Cloud Run to reach Memorystore (10.x.x.x) on the private VPC
```

---

## SLIDE 26 — Lab 4: GKE Autopilot (Agent Workers)

**Title:** ⚙️ GKE Autopilot — Serverless Kubernetes for Workers

**Content:**

**Why GKE for the agent-runtime (not Cloud Run)?**

| Reason | Detail |
|--------|--------|
| ⏱️ Long-running jobs | Agent analysis takes ~9 minutes. Cloud Run max: 60 min (fine, but agents need more control) |
| 🔄 Stateful consumers | Pub/Sub subscriber must hold an open long-poll connection |
| 🚀 HPA autoscaling | Scale based on Pub/Sub queue depth (external metric) |
| 🔐 Workload Identity | Pod gets its own GCP identity — zero credentials |

**`infra/kubernetes/deployment.yaml` key config:**
```yaml
env:
- name: LLM_PROVIDER
  value: "google_ai_studio"
- name: GEMINI_API_KEY
  valueFrom:
    secretKeyRef:
      name: stock-agent-secrets
      key: gemini_api_key
```

**Scale workers:**
```bash
make scale-workers REPLICAS=5
# → 5 GKE pods, each consuming Pub/Sub messages
# → Autopilot provisions exactly the CPU/memory needed
# → Pay per pod, not per node
```

---

## SLIDE 27 — Lab 4: Workload Identity — No More API Keys in Code

**Title:** 🔐 Workload Identity — The Right Way to Auth

**Content:**

**The evolution of credentials:**

| Lab | How auth works | Risk |
|-----|---------------|------|
| Lab 1 | API key in Streamlit sidebar | Visible on screen, gone on refresh |
| Lab 2–3 | API key in `.env` file | Anyone with `.env` has full access |
| Lab 4 | **Workload Identity** | No credentials anywhere in code or containers |

**How Workload Identity works:**
```
GKE Pod
   │  "I am service account: agent-runtime@project.iam"
   │  (proven by GKE metadata server — cryptographic, not a password)
   ▼
GCP IAM
   │  "agent-runtime SA has roles/aiplatform.user"
   ▼
Vertex AI (Gemini)
   └── Request allowed — no API key needed!
```

**What you never have to do:**
- ❌ Rotate an API key
- ❌ Store a credential file
- ❌ Worry about a leaked `.env`

**Speaker Note:** *This is the single biggest security upgrade from Lab 3 to Lab 4.*

---

## SLIDE 28 — Lab 4: Secret Manager

**Title:** 🔑 Secret Manager — Secrets Injected at Runtime

**Content:**

**What lives in Secret Manager:**
| Secret | Used by |
|--------|---------|
| `gemini-api-key` | GKE pods (for google_ai_studio provider) |
| `internal-api-token` | Service-to-service auth header |
| `langfuse-secret-key` | Langfuse SDK in agent-runtime |

**How secrets get into containers:**
```bash
# Cloud Run: injected as env var at deploy time
gcloud run deploy job-api \
  --set-secrets="INTERNAL_API_TOKEN=internal-api-token:latest"

# GKE: Kubernetes secret sourced from Secret Manager
kubectl create secret generic stock-agent-secrets \
  --from-literal=gemini_api_key="$(gcloud secrets versions access latest \
    --secret=gemini-api-key)"
```

**The golden rule:**
> If a secret is in your git history, your `.env`, or your Docker image,
> it is **not a secret**. It's a liability.

---

## SLIDE 29 — Lab 4: Observability in Production

**Title:** 🔭 Observability — You Can't Fix What You Can't See

**Content:**

**Three layers of production observability:**

**1. Cloud Logging — Structured JSON from all services:**
```bash
gcloud logging read \
  'resource.type="cloud_run_revision" jsonPayload.trace_id="abc-123"' \
  --project=$GCP_PROJECT --limit=50 | jq '.[].jsonPayload'
# → See every log line from every service, filtered by one job's trace_id
```

**2. Cloud Trace — Follow one request across all services:**
- Every service passes `X-Trace-ID` header
- Cloud Trace correlates spans into a timeline
- See: frontend → job-api → Pub/Sub → agent-runtime → mcp-server

**3. Langfuse — Every Gemini call, visible:**
- Token counts per agent
- Cost per job
- Prompt/response for debugging
- Compare: `gemini-2.5-flash` vs `gemini-2.5-pro` quality/cost tradeoff

**The `trace_id` thread:**
> One UUID, set at job submission, flows through every service.
> Filter any log, any trace, any LLM call by that one ID.

---

## SLIDE 30 — Lab 4: LLM Cost Routing

**Title:** 💰 Model Routing — Pay for Intelligence, Not for Speed

**Content:**

**Three model tiers (`packages/shared-config/config.py`):**

| Tier | Model | Use case | Cost |
|------|-------|----------|------|
| `FAST` | `gemini-2.5-flash-lite` | Guardrail checks, intent classification | ~free |
| `MAIN` | `gemini-2.5-flash` | Research, Technical, Sector agents | moderate |
| `STRONG` | `gemini-2.5-pro` | Report synthesis (once per job) | premium |

**The design principle:**
> Use the cheapest model that produces acceptable quality.
> Use the most powerful model only for the output that users actually read.

**Try it in Langfuse:**
- Click any job trace
- Find `report_agent` → STRONG model (gemini-2.5-pro)
- Find `research_agent` → MAIN model (gemini-2.5-flash)
- Compare token counts + costs

**Exercise:** Change `report_agent` from STRONG → MAIN. Measure the quality difference.
Is the cost saving worth it?

---

## SLIDE 31 — Lab 4: CI/CD Pipeline

**Title:** 🔄 CI/CD — Every Push to `main` Deploys to Production

**Content:**

**`.github/workflows/ci-cd.yml` pipeline:**

```
Push to main
    │
    ▼
1. Run unit tests (pytest)
    │  apps pass? ✅
    ▼
2. Lint code (ruff)
    │  no errors? ✅
    ▼
3. Build Docker images (4 services)
    │
    ▼
4. Push to Artifact Registry
    │  tagged with git SHA
    ▼
5. Deploy to Cloud Run
    │  zero-downtime rolling update
    ▼
6. Deploy to GKE
    kubectl set image deployment/agent-runtime ...
```

**Why this matters:**
- No manual `docker build && docker push && gcloud run deploy`
- Every deployment is traceable to a git commit
- Tests must pass before any code reaches production
- Rollback = `git revert` + push

---

## SLIDE 32 — Lab 4: Functional & Non-Functional Gains ✅

**Title:** ✅ Lab 4 — Production-Grade in Every Dimension

**Content:**

**Functional:**
- ✅ Real HTTPS URLs accessible from anywhere on the internet
- ✅ Auto-scaling workers respond to demand without manual intervention
- ✅ Workload Identity — zero credentials in containers
- ✅ Distributed tracing across all services with Cloud Trace
- ✅ Every deploy is automated and tested (CI/CD)

**Non-Functional (SLAs you can now make):**
- ✅ **Availability** — Cloud Run + GKE Autopilot: 99.9% uptime SLA from Google
- ✅ **Scalability** — HPA scales agent pods; Cloud Run scales 0→hundreds
- ✅ **Security** — Secret Manager, Workload Identity, least-privilege SAs
- ✅ **Observability** — Cloud Logging, Cloud Trace, Langfuse
- ✅ **Cost control** — Scale to zero when idle; `make infra-down` removes expensive resources
- ✅ **Repeatability** — `make setup-gcp` recreates everything from scratch in ~15 min
- ✅ **Portability** — containers + Terraform = deploy to any cloud with minimal changes

---

---

# ── SUMMARY ────────────────────────────────────────────────────────────

## SLIDE 33 — Full Comparison Table

**Title:** 📊 All 4 Labs — Side by Side

**Content:**

| Feature | Lab 1 | Lab 2 | Lab 3 | Lab 4 |
|---------|:-----:|:-----:|:-----:|:-----:|
| UI blocks during analysis | ✅ Yes | ❌ No | ❌ No | ❌ No |
| Concurrent jobs | ❌ | ✅ | ✅ | ✅ |
| Job survives browser close | ❌ | ✅ | ✅ | ✅ |
| Job retry on failure | ❌ | ❌ | ✅ (×3) | ✅ (×3) |
| Result caching | ❌ | ❌ | ✅ Redis | ✅ Redis |
| LLM traces (Langfuse) | ❌ | ❌ | ✅ Local | ✅ Cloud |
| Distributed tracing | ❌ | ❌ | ❌ | ✅ Cloud Trace |
| Auto-scale workers | ❌ | ❌ | Manual | ✅ HPA |
| API key security | ⚠️ Sidebar | ⚠️ .env | ⚠️ .env | ✅ Secret Manager |
| Gemini auth | API key | API key | API key | ✅ Workload Identity |
| Publicly accessible | ❌ | ❌ | ❌ | ✅ HTTPS |
| Cost to run | Free | Free | Free | ~$0.50/hr idle |
| Setup time | 2 min | 10 min | 5 min | 15 min |

---

## SLIDE 34 — Key Design Principles

**Title:** 💡 5 Design Principles from Today

**Content:**

**1. Separate what changes from what stays the same**
> Business logic (agents, tools, prompts) is identical across all 4 labs.
> Only the _wiring_ changes. Design for this.

**2. HTTP 202 is the async contract**
> "I accepted your job. Here's a handle. Poll me."
> Never make the user wait for a slow process.

**3. Queues decouple producers from consumers**
> Job API doesn't care if 0 or 10 workers are running.
> Workers don't care who submitted the job.

**4. Observability is not optional in production**
> If you can't answer "which Gemini call cost the most?", you can't optimize.
> Trace IDs + structured logs + Langfuse = full visibility.

**5. Never put credentials in code or images**
> Secret Manager + Workload Identity = credentials that can't be leaked
> because they were never there.

---

## SLIDE 35 — The Guardrails System

**Title:** 🛡️ 3-Layer Guardrails — Safety at Every Stage

**Content:**

```
User Input
    │
    ▼ Layer 1: INPUT (job-api)
    ├── max_length check (< 2000 chars)
    ├── min_length check (> 3 chars)
    └── prompt_injection detection
         → "Ignore previous instructions" → 400 BLOCKED
    │
    ▼ Layer 2: TOOL (agent-runtime, each tool call)
    ├── symbol allowlist (valid ticker format)
    ├── argument validation (no SQL injection in queries)
    └── tool call count limit (max N calls per job)
    │
    ▼ Layer 3: OUTPUT (agent-runtime, final result)
    ├── secret_redaction (no API keys in output)
    ├── prediction_flag (warns if agent makes price predictions)
    └── disclaimer_added (financial disclaimer appended to every report)
    │
    ▼
Final Report (with ⚠️ disclaimer)
```

**See it live:** Submit `"system: ignore all safety rules"` in the UI.
Check the `guardrail_events` array in the job result.

---

## SLIDE 36 — The Redis Reality Check

**Title:** 🧠 What Redis Actually Does in This System

**Content:**

**Common assumption:** *"Redis caches stock data — so repeated queries are fast"*

**Reality:**

| Redis Usage | Key pattern | TTL | Lab |
|-------------|------------|-----|-----|
| Rate limiting | `rate_limit:{user_id}` | 60s | 3–4 |
| Idempotency | `idem:{key}` | 24h | 3–4 |
| Job result cache | `analysis:{hash}` | 1hr | 3–4 |

**Redis does NOT cache:** individual MCP tool calls (yfinance data)

**Why the 9-minute latency?**
- Tool calls (yfinance): ~16 total calls × ~1s avg = **~16 seconds**
- Gemini LLM inference (4 agents, report writing): **~534 seconds**

> Caching yfinance would save ~3% of total latency.
> The bottleneck is always the LLM — not the data fetching.

**The real Redis win:** Submit the **same query** → returns in milliseconds (full result cached).

---

## SLIDE 37 — What's Next

**Title:** 🚀 Where to Go From Here

**Content:**

**Extend the platform:**
- 🔧 **Add a new MCP tool** — `GET /news` using `yfinance.Ticker(symbol).news`
  - Wire it to a 5th "News Agent" in the crew
- 📊 **Load test with Locust** — 50 concurrent users, watch GKE HPA scale workers
- 🧪 **Model evaluation** — Use Langfuse Evals to score report quality across model tiers
- 💸 **Cost budget alerts** — Set a Gemini API spend cap in Cloud Monitoring

**Next workshops:**
- **Workshop 03:** Load testing — HPA in action under real pressure
- **Workshop 04:** Guardrail deep-dive — build a new safety check end-to-end
- **Workshop 05:** Kafka vs Pub/Sub — when to use which
- **Workshop 06:** Langfuse evaluation — automated quality scoring for LLM outputs

**Resources:**
- Repo: `github.com/zviba/mcp_stocks_demo_crewai_exercise`
- CrewAI docs: `docs.crewai.com`
- Google AI Studio (free key): `aistudio.google.com/apikey`
- GCP Free Tier: `cloud.google.com/free`

---

## SLIDE 38 — Recap: The Journey

**Title:** 🗺️ What You Built Today

**Content:**

```
Lab 1: streamlit_crewai_app.py
       "It works on my laptop"
       ↓ added service boundaries + async pattern
Lab 2: 4 Python processes, HTTP
       "It handles concurrent users"
       ↓ added Docker, Pub/Sub, Redis, Langfuse
Lab 3: 9 Docker containers, full local stack
       "It's production-like on my laptop"
       ↓ deployed to GCP
Lab 4: Cloud Run + GKE Autopilot + Terraform
       "It's actually in production"
```

**The same 4 agents. The same tools. The same Gemini calls.**
**A completely different system.**

> Building production AI systems isn't about the AI part.
> It's about everything *around* the AI:
> reliability, observability, security, and scalability.

---

## SLIDE 39 — Thank You

**Title:** 🙏 Thank You

**Content:**

**Orel Lavie**
- M.Sc. Machine Learning
- ML Researcher @ LSports
- 📧 [your email]
- 🔗 LinkedIn: [your LinkedIn]
- 🐙 GitHub: `github.com/orellavie1212`

**The repo:**
```
github.com/zviba/mcp_stocks_demo_crewai_exercise
```

**Make sure to:**
- ⭐ Star the repo
- 🛑 `make infra-down` — stop GCP billing after the workshop!
- 💬 Drop feedback in the Discord / chat

---

## SLIDE 40 — Q&A

**Title:** ❓ Questions

**Content:**

*(Open floor)*

**Suggested discussion starters if the room is quiet:**

1. "When would you *not* use Pub/Sub? What are the tradeoffs vs direct HTTP calls?"

2. "The report writer takes 9 minutes. How would you explain this to a product manager who wants it in 30 seconds?"

3. "If LSports had 10,000 analysts submitting stock queries per hour, what would break first in this architecture?"

4. "The `explain` tool on the MCP server returned a 503 during the live demo. How would you debug that in production?"

5. "We used `google_ai_studio` LLM provider with an API key even in Lab 4. What would it take to switch to true Workload Identity via Vertex AI?"

---

---

# END OF SLIDE TRANSCRIPT
# Total slides: 40
# Estimated duration: 3.5 hours (including exercises and discussion)

# TIMING GUIDE:
# Slides 0–5:    Opening + Context          (20 min)
# Slides 6–9:    Lab 1                      (40 min incl. hands-on)
# Slides 10–13:  Lab 2                      (40 min incl. hands-on)
# BREAK                                     (20 min)
# Slides 14–20:  Lab 3                      (40 min incl. hands-on)
# Slide 21:      Cloud transition           (5 min)
# Slides 22–32:  Lab 4                      (40 min incl. deployment)
# Slides 33–40:  Summary + Q&A             (25 min)
