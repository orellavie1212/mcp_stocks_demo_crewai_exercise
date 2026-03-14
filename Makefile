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

GCP_PROJECT  ?= your-gcp-project-id
GCP_REGION   ?= us-central1
AR_REPO      ?= stock-agent
AR_HOST      ?= $(GCP_REGION)-docker.pkg.dev
IMAGE_PREFIX := $(AR_HOST)/$(GCP_PROJECT)/$(AR_REPO)

SERVICES     := mcp-server job-api agent-runtime frontend-streamlit langfuse

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# =============================================================================
# LOCAL DEVELOPMENT
# =============================================================================

# Docker Compose helper — always load .env from repo root
DC = docker compose --env-file .env -f docker/docker-compose.yml

up: ## Start full local stack (all services + Redis + Pub/Sub emulator + Langfuse)
	$(DC) up --build -d
	@echo ""
	@echo "✅ Stack started:"
	@echo "  Streamlit UI  : http://localhost:8501"
	@echo "  Job API       : http://localhost:8000/docs"
	@echo "  MCP Server    : http://localhost:8001/docs"
	@echo "  Langfuse UI   : http://localhost:3000"
	@echo ""

down: ## Stop local stack
	$(DC) down

logs: ## Tail logs from all services
	$(DC) logs -f

restart: ## Restart a specific service (SERVICE=agent-runtime)
	$(DC) restart $(SERVICE)

up-simple: ## Start simplified stack (sync mode, no Pub/Sub, no Redis)
	docker compose --env-file .env -f docker/docker-compose.simple.yml up --build -d

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

infra-down: ## DESTROY GCP infrastructure (stops billing — keeps Firestore/Pub/Sub)
	@echo "⚠️  This will destroy GKE, Redis, and Cloud SQL. Firestore/Pub/Sub are kept."
	@read -p "Are you sure? [y/N]: " confirm && [ "$$confirm" = "y" ]
	cd infra/terraform && terraform destroy -auto-approve \
	  -var="project_id=$(GCP_PROJECT)" \
	  -var="region=$(GCP_REGION)" \
	  -target=google_container_cluster.agent_cluster \
	  -target=google_redis_instance.cache \
	  -target=google_sql_database_instance.langfuse

# =============================================================================
# GCP DEPLOYMENT — CLOUD RUN (Stages 2-3)
# =============================================================================

deploy-run: build push ## Build, push, and deploy all services to Cloud Run
	./scripts/deploy-cloud-run.sh --project=$(GCP_PROJECT) --region=$(GCP_REGION)

# =============================================================================
# GCP DEPLOYMENT — GKE AUTOPILOT (Stage 4)
# =============================================================================

deploy-gke: push ## Deploy agent-runtime workers to GKE Autopilot
	gcloud container clusters get-credentials agent-cluster \
	  --region=$(GCP_REGION) --project=$(GCP_PROJECT)
	kubectl apply -f infra/kubernetes/
	kubectl rollout status deployment/agent-runtime -n stock-agent

scale-workers: ## Scale agent-runtime workers (REPLICAS=5)
	kubectl scale deployment/agent-runtime -n stock-agent --replicas=$(REPLICAS)

# =============================================================================
# SECRETS & SETUP
# =============================================================================

setup-gcp: ## One-command GCP project setup (run once per project)
	./scripts/setup-gcp.sh --project=$(GCP_PROJECT) --region=$(GCP_REGION)

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
