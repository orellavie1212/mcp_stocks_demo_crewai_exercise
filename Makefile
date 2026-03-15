# =============================================================================
# Stock Agent Platform — Makefile
# Production monorepo for CrewAI + MCP + GCP
# =============================================================================
# Usage:
#   make up          → start full local stack (docker compose)
#   make down        → stop local stack
#   make deploy-run  → deploy all services to Cloud Run (GCP)
#   make deploy-gke  → deploy agent-runtime to GKE Autopilot
#   make infra-up    → provision GCP infrastructure with Terraform
#   make infra-down  → destroy GCP infrastructure (stop billing)
# =============================================================================

.PHONY: help up down logs test lint build push deploy-run deploy-gke \
        infra-up infra-down setup-gcp seed-secrets clean

# Load environment variables from .env if it exists
-include .env
export

GCP_PROJECT      ?= your-gcp-project-id
GCP_REGION       ?= us-central1
GEMINI_API_KEY   ?=
AR_REPO          ?= stock-agent
AR_HOST          ?= $(GCP_REGION)-docker.pkg.dev
IMAGE_PREFIX     := $(AR_HOST)/$(GCP_PROJECT)/$(AR_REPO)

SERVICES     := mcp-server job-api agent-runtime frontend-streamlit langfuse

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# =============================================================================
# LOCAL DEVELOPMENT
# =============================================================================

# Docker Compose helper — always load .env from repo root
DC = docker compose --env-file .env -f docker/docker-compose.yml

lab3-up: ## Start full local stack (all services + Redis + Pub/Sub emulator + Langfuse)
	$(DC) up --build -d
	@echo ""
	@echo "✅ Stack started:"
	@echo "  Streamlit UI  : http://localhost:8501"
	@echo "  Job API       : http://localhost:8000/docs"
	@echo "  MCP Server    : http://localhost:8001/docs"
	@echo "  Langfuse UI   : http://localhost:3000"
	@echo ""

lab3-down: ## Stop local stack
	$(DC) down

logs: ## Tail logs from all services
	$(DC) logs -f

lab3-restart: ## Restart a specific service (SERVICE=agent-runtime)
	$(DC) restart $(SERVICE)

lab3-up-simple: ## Start simplified stack (sync mode, no Pub/Sub, no Redis)
	docker compose --env-file .env -f docker/docker-compose.simple.yml up --build -d

# =============================================================================
# LAB 2 — Native local dev (no Docker, direct HTTP between services)
# Run each target in a separate terminal tab.
# Order: lab2-mcp → lab2-api → lab2-worker → lab2-ui
# =============================================================================

lab2-mcp: ## Lab 2 ➊  MCP server on :8001  (open a new terminal first)
	cd apps/mcp-server && \
	  GEMINI_API_KEY=$(GEMINI_API_KEY) LOG_FORMAT=text \
	  uvicorn server:app --host 0.0.0.0 --port 8001

lab2-api: ## Lab 2 ➋  Job API on :8000  (open a new terminal)
	cd apps/job-api && \
	  GEMINI_API_KEY=$(GEMINI_API_KEY) \
	  AGENT_RUNTIME_URL=http://localhost:8002 \
	  LOG_FORMAT=text \
	  uvicorn main:app --host 0.0.0.0 --port 8000

lab2-worker: ## Lab 2 ➌  Agent-runtime worker (HTTP mode) on :8002  (open a new terminal)
	cd apps/agent-runtime && \
	  GEMINI_API_KEY=$(GEMINI_API_KEY) \
	  MCP_SERVER_URL=http://localhost:8001 \
	  JOB_API_URL=http://localhost:8000 \
	  LOG_FORMAT=text \
	  python worker.py --mode http

lab2-ui: ## Lab 2 ➍  Streamlit frontend on :8501  (open a new terminal)
	cd apps/frontend-streamlit && \
	  JOB_API_URL=http://localhost:8000 \
	  streamlit run app.py

# =============================================================================
# TESTING
# =============================================================================

test: ## Run all unit tests
	cd packages/shared-models && python -m pytest ../../tests/unit/test_models/ -v
	cd packages/shared-guardrails && python -m pytest ../../tests/unit/test_guardrails/ -v
	cd apps/mcp-server && python -m pytest ../../tests/unit/test_mcp_server/ -v

test-integration: ## Run integration tests (requires running stack)
	python -m pytest tests/integration/ -v --timeout=60

lint: ## Lint all Python code
	ruff check apps/ packages/ tests/
	mypy apps/ packages/ --ignore-missing-imports

format: ## Format all Python code
	ruff format apps/ packages/ tests/

# =============================================================================
# BUILD & PUSH DOCKER IMAGES
# =============================================================================

build: ## Build all Docker images
	@for svc in mcp-server job-api agent-runtime frontend-streamlit; do \
	  echo "🔨 Building $$svc..."; \
	  docker build -t stock-agent-$$svc:latest -f apps/$$svc/Dockerfile apps/$$svc; \
	done

push: ## Push images to Artifact Registry
	gcloud auth configure-docker $(AR_HOST) --quiet
	@for svc in mcp-server job-api agent-runtime frontend-streamlit; do \
	  echo "📤 Pushing $$svc..."; \
	  docker tag stock-agent-$$svc:latest $(IMAGE_PREFIX)/$$svc:latest; \
	  docker push $(IMAGE_PREFIX)/$$svc:latest; \
	done

# =============================================================================
# GCP INFRASTRUCTURE (TERRAFORM)
# =============================================================================

infra-init: ## Initialize Terraform
	cd infra/terraform && terraform init

infra-plan: ## Preview GCP infrastructure changes
	cd infra/terraform && terraform plan \
	  -var="project_id=$(GCP_PROJECT)" \
	  -var="region=$(GCP_REGION)"

infra-up: ## Provision full GCP infrastructure
	cd infra/terraform && terraform apply -auto-approve \
	  -var="project_id=$(GCP_PROJECT)" \
	  -var="region=$(GCP_REGION)"
	@echo "✅ Infrastructure provisioned. Run 'make deploy-run' to deploy services."

infra-down: ## Full GCP teardown — deletes ALL resources (Cloud Run x5, GKE, Redis, Cloud SQL, Pub/Sub, secrets)
	chmod +x scripts/teardown-gcp.sh
	./scripts/teardown-gcp.sh --project=$(GCP_PROJECT) --region=$(GCP_REGION)

# =============================================================================
# GCP DEPLOYMENT — CLOUD RUN (Stages 2-3)
# =============================================================================

deploy-run: build push ## Build, push, and deploy all services to Cloud Run
	./scripts/deploy-cloud-run.sh --project=$(GCP_PROJECT) --region=$(GCP_REGION)

# =============================================================================
# GCP DEPLOYMENT — GKE AUTOPILOT (Stage 4)
# =============================================================================

deploy-gke: push ## Deploy agent-runtime workers to GKE Autopilot (uses envsubst to fill deployment.yaml variables)
	gcloud container clusters get-credentials agent-cluster \
	  --region=$(GCP_REGION) --project=$(GCP_PROJECT)
	@echo "Reading Cloud Run URLs, Langfuse, and Redis from GCP..."
	$(eval MCP_URL          := $(shell gcloud run services describe mcp-server  --region=$(GCP_REGION) --project=$(GCP_PROJECT) --format="value(status.url)" 2>/dev/null))
	$(eval JOB_API_URL      := $(shell gcloud run services describe job-api      --region=$(GCP_REGION) --project=$(GCP_PROJECT) --format="value(status.url)" 2>/dev/null))
	$(eval LANGFUSE_URL     := $(shell gcloud run services describe langfuse      --region=$(GCP_REGION) --project=$(GCP_PROJECT) --format="value(status.url)" 2>/dev/null))
	$(eval LANGFUSE_PK      := $(shell gcloud secrets versions access latest --secret=langfuse-public-key --project=$(GCP_PROJECT) 2>/dev/null))
	$(eval REDIS_HOST       := $(shell cd infra/terraform && terraform output -raw redis_host 2>/dev/null))
	$(eval REDIS_PORT       := $(shell cd infra/terraform && terraform output -raw redis_port 2>/dev/null || echo "6379"))
	GCP_PROJECT=$(GCP_PROJECT) GCP_REGION=$(GCP_REGION) \
	  MCP_URL=$(MCP_URL) JOB_API_URL=$(JOB_API_URL) \
	  REDIS_URL=redis://$(REDIS_HOST):$(REDIS_PORT)/0 \
	  LANGFUSE_URL=$(LANGFUSE_URL) LANGFUSE_PUBLIC_KEY=$(LANGFUSE_PK) \
	  envsubst < infra/kubernetes/deployment.yaml | kubectl apply -f -
	kubectl rollout status deployment/agent-runtime -n stock-agent --timeout=300s

scale-workers: ## Scale agent-runtime workers (REPLICAS=5)
	kubectl scale deployment/agent-runtime -n stock-agent --replicas=$(REPLICAS)

# =============================================================================
# SECRETS & SETUP
# =============================================================================

setup-gcp: ## One-command GCP project setup — provisions infra, builds images, deploys Cloud Run + GKE (run once)
	chmod +x scripts/setup-gcp.sh scripts/teardown-gcp.sh scripts/deploy-cloud-run.sh
	./scripts/setup-gcp.sh \
	  --project=$(GCP_PROJECT) \
	  --region=$(GCP_REGION) \
	  --gemini-api-key=$(GEMINI_API_KEY)

teardown: ## DESTROY all GCP resources and start from zero (deletes Cloud Run, GKE, Redis, SQL, Pub/Sub, secrets)
	chmod +x scripts/teardown-gcp.sh
	./scripts/teardown-gcp.sh --project=$(GCP_PROJECT) --region=$(GCP_REGION)

teardown-yes: ## Non-interactive full teardown (for CI/automation)
	chmod +x scripts/teardown-gcp.sh
	./scripts/teardown-gcp.sh --project=$(GCP_PROJECT) --region=$(GCP_REGION) --yes

seed-secrets: ## Seed Secret Manager with values from .env.production
	./scripts/seed-secrets.sh --project=$(GCP_PROJECT)

# =============================================================================
# UTILITIES
# =============================================================================

clean: ## Remove local Docker images and cache
	docker compose -f docker/docker-compose.yml down --volumes --rmi local
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true

show-urls: ## Show all deployed service URLs
	@echo "Cloud Run URLs:"
	@gcloud run services list --project=$(GCP_PROJECT) --region=$(GCP_REGION) \
	  --format="table(metadata.name, status.url)"
