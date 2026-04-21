#!/usr/bin/env bash
# =============================================================================
# setup-gcp.sh — One-command GCP project setup (Lab 4)
# =============================================================================
# Usage:
#   ./scripts/setup-gcp.sh --project=my-project-id [--region=us-central1]
#
# Prerequisites:
#   brew install google-cloud-sdk terraform gettext  # for envsubst
#   gcloud auth login && gcloud auth application-default login
#   kubectl (gcloud components install kubectl)
#
# What this does (zero manual steps):
#   1. Checks prerequisites
#   2. Runs terraform apply (GKE, Redis, Cloud SQL, VPC, Pub/Sub, IAM, secrets)
#   3. Seeds Secret Manager (idempotent — safe to re-run)
#   4. Builds + pushes Docker images to Artifact Registry
#   5. Deploys: MCP Server, Job API, Agent Runtime, Frontend to Cloud Run
#   6. Deploys Langfuse to Cloud Run (backed by Cloud SQL)
#   7. Sets up GKE: namespace, ConfigMap, K8s Secret, deploys workers (pubsub mode)
#   8. Prints all URLs
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Args
# ---------------------------------------------------------------------------
PROJECT_ID=""
REGION="us-central1"
GEMINI_API_KEY=""

for arg in "$@"; do
  case $arg in
    --project=*)         PROJECT_ID="${arg#*=}" ;;
    --region=*)          REGION="${arg#*=}" ;;
    --gemini-api-key=*)  GEMINI_API_KEY="${arg#*=}" ;;
    *) echo "Unknown argument: $arg" && exit 1 ;;
  esac
done

if [[ -z "$PROJECT_ID" ]]; then
  PROJECT_ID=$(gcloud config get-value project 2>/dev/null || echo "")
  [[ -z "$PROJECT_ID" ]] && echo "❌ --project required" && exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
AR_HOST="${REGION}-docker.pkg.dev"
AR_REPO="${AR_HOST}/${PROJECT_ID}/stock-agent"

echo ""
echo "============================================================"
echo "  Stock Agent Platform — Lab 4 GCP Setup"
echo "============================================================"
echo "  Project  : $PROJECT_ID"
echo "  Region   : $REGION"
echo "  Registry : $AR_REPO"
echo "============================================================"
echo ""

# ---------------------------------------------------------------------------
# Prerequisite check
# ---------------------------------------------------------------------------
echo "🔍 Checking prerequisites..."
for tool in gcloud terraform docker kubectl envsubst; do
  if ! command -v "$tool" &>/dev/null; then
    echo "❌ Missing: $tool"
    [[ "$tool" == "envsubst" ]] && echo "   Fix: brew install gettext && brew link gettext --force"
    [[ "$tool" == "kubectl"  ]] && echo "   Fix: gcloud components install kubectl"
    exit 1
  fi
done
gcloud auth application-default print-access-token &>/dev/null || {
  echo "❌ Not authenticated. Run: gcloud auth application-default login"; exit 1; }
echo "  ✅ All prerequisites OK"

# ---------------------------------------------------------------------------
# Helper: create a Secret Manager version only on first run (idempotent)
# Re-running setup never overwrites existing secrets — keys stay consistent.
# ---------------------------------------------------------------------------
secret_once() {
  local name="$1" val="$2"
  if gcloud secrets versions access latest --secret="$name" \
      --project="$PROJECT_ID" &>/dev/null 2>&1; then
    echo "  ⏭️  $name already set (keeping existing)"
  else
    echo -n "$val" | gcloud secrets versions add "$name" \
      --data-file=- --project="$PROJECT_ID" 2>/dev/null || \
    echo -n "$val" | gcloud secrets create "$name" \
      --data-file=- --project="$PROJECT_ID"
    echo "  ✅ $name seeded"
  fi
}

# ---------------------------------------------------------------------------
# Step 1: GCP project config
# ---------------------------------------------------------------------------
echo ""
echo "📋 Step 1: Configuring GCP project..."
gcloud config set project "$PROJECT_ID"
gcloud config set compute/region "$REGION"
echo "  ✅ Project: $PROJECT_ID"

# ---------------------------------------------------------------------------
# Step 1.5: Enable bootstrap APIs BEFORE Terraform runs
# ---------------------------------------------------------------------------
# Teaching note: Terraform's `import` block in main.tf (Firestore default DB)
# and the `data "google_project"` lookup both run during plan/refresh — BEFORE
# google_project_service.apis has a chance to enable APIs. On a brand-new
# project this fails with 403 SERVICE_DISABLED on firestore.googleapis.com.
# Enabling the minimum bootstrap set here makes `terraform apply` work on
# fresh projects. `gcloud services enable` is idempotent — no-op on re-runs.
echo ""
echo "🔌 Step 1.5: Enabling bootstrap GCP APIs..."
gcloud services enable \
  serviceusage.googleapis.com \
  cloudresourcemanager.googleapis.com \
  firestore.googleapis.com \
  compute.googleapis.com \
  --project="${PROJECT_ID}"
echo "  ✅ Bootstrap APIs enabled"

# ---------------------------------------------------------------------------
# Step 2: Terraform — provision ALL infrastructure
# ---------------------------------------------------------------------------
echo ""
echo "🏗️  Step 2: Terraform apply..."
echo "   First run ~15 min | Re-run ~3 min"
echo ""

cd "${REPO_ROOT}/infra/terraform"
terraform init -upgrade 2>&1 | grep -E "(Initializing|Installed|Warning|Error|Upgrade)" || true

# Teaching note: Firestore idempotency is handled by an `import` block in main.tf.
# GCP forbids re-creating the (default) database for up to 7 days after deletion.
# The import block (Terraform ≥ 1.5) tells terraform apply to ADOPT an existing DB
# rather than create it → no 409 conflict on re-runs.  Nothing to do here in shell.

terraform apply -auto-approve \
  -var="project_id=${PROJECT_ID}" \
  -var="region=${REGION}"

# Read infrastructure values from Terraform outputs
REDIS_HOST=$(terraform output -raw redis_host)
REDIS_PORT=$(terraform output -raw redis_port)
REDIS_URL="redis://${REDIS_HOST}:${REDIS_PORT}/0"
LANGFUSE_DB_CONN=$(terraform output -raw langfuse_db_connection_name)
LANGFUSE_DB_PASS=$(terraform output -raw langfuse_db_password)
# Admin password is fully managed by Terraform — read it here, no manual step
LF_ADMIN_PASS=$(terraform output -raw langfuse_admin_password)

cd "${REPO_ROOT}"
echo ""
echo "  ✅ Infrastructure ready"
echo "     Redis          : ${REDIS_HOST}:${REDIS_PORT}"
echo "     Cloud SQL conn : ${LANGFUSE_DB_CONN}"

# ---------------------------------------------------------------------------
# Step 3: Seed Secret Manager (idempotent — safe to re-run)
# ---------------------------------------------------------------------------
echo ""
echo "🔐 Step 3: Seeding secrets..."

# Internal API token
secret_once "internal-api-token" "$(openssl rand -base64 32)"

# Langfuse project API keys (pk-lf / sk-lf format required by Langfuse)
# Teaching note: These MUST stay consistent — agents use sk to submit traces,
# and Langfuse DB was initialized with them. Changing them = broken traces.
LF_PK="pk-lf-$(openssl rand -hex 16)"
LF_SK="sk-lf-$(openssl rand -hex 32)"
secret_once "langfuse-public-key" "$LF_PK"
secret_once "langfuse-secret-key" "$LF_SK"

# Langfuse application secrets (MUST be stable — changing breaks sessions/DB encryption)
secret_once "langfuse-nextauth-secret" "$(openssl rand -base64 32)"
secret_once "langfuse-encryption-key"  "$(openssl rand -hex 32)"

# Langfuse DATABASE_URL — Cloud SQL Proxy socket path (provided by Cloud Run)
# Teaching note: Prisma requires a non-empty host even for Unix sockets.
# Use @localhost — the ?host= query param overrides it with the actual socket path.
# Wrong:   postgresql://user:pass@/db?host=/cloudsql/...   ← Prisma P1013 (empty host)
# Correct: postgresql://user:pass@localhost/db?host=/cloudsql/...
LANGFUSE_DB_URL="postgresql://langfuse:${LANGFUSE_DB_PASS}@localhost/langfuse?host=/cloudsql/${LANGFUSE_DB_CONN}"
# Always update (not secret_once) — the password comes from Terraform and may rotate.
echo -n "$LANGFUSE_DB_URL" | gcloud secrets versions add "langfuse-db-url" \
  --data-file=- --project="$PROJECT_ID" 2>/dev/null || \
echo -n "$LANGFUSE_DB_URL" | gcloud secrets create "langfuse-db-url" \
  --data-file=- --project="$PROJECT_ID"
echo "  ✅ langfuse-db-url updated"

# Gemini API key — seed if provided via --gemini-api-key, otherwise warn
# Teaching note: if GEMINI_API_KEY is passed at make time it lands here in one step.
# secret_once is idempotent: re-runs never overwrite an existing key.
if [[ -n "$GEMINI_API_KEY" ]]; then
  secret_once "gemini-api-key" "$GEMINI_API_KEY"
elif ! gcloud secrets versions access latest --secret="gemini-api-key" \
    --project="$PROJECT_ID" &>/dev/null 2>&1; then
  echo "  ⏭️  gemini-api-key not set (Vertex AI will be used — LLM_PROVIDER=vertex_ai)"
else
  echo "  ⏭️  gemini-api-key already set (keeping existing)"
fi

# Read back the final values (handles both first-run and re-run)
LF_PUBLIC_KEY=$(gcloud secrets versions access latest \
  --secret="langfuse-public-key" --project="$PROJECT_ID")
LF_SECRET_KEY=$(gcloud secrets versions access latest \
  --secret="langfuse-secret-key" --project="$PROJECT_ID")
LF_NEXTAUTH_SECRET=$(gcloud secrets versions access latest \
  --secret="langfuse-nextauth-secret" --project="$PROJECT_ID")
LF_ENCRYPTION_KEY=$(gcloud secrets versions access latest \
  --secret="langfuse-encryption-key" --project="$PROJECT_ID")
INTERNAL_TOKEN=$(gcloud secrets versions access latest \
  --secret="internal-api-token" --project="$PROJECT_ID")

# Read Gemini API key for GKE secret (needed by deployment.yaml)
# Teaching note: get_llm() uses LangChain's ChatVertexAI for vertex_ai, but newer
# CrewAI wraps LLMs via LiteLLM and doesn't understand langchain objects ("unknown
# object type" error). google_ai_studio + GEMINI_API_KEY is fully compatible with
# CrewAI's LiteLLM layer and requires no Workload Identity complexity.
GEMINI_KEY_FOR_K8S=$(gcloud secrets versions access latest \
  --secret="gemini-api-key" --project="$PROJECT_ID" 2>/dev/null || echo "")

echo "  ✅ Secrets ready"

# ---------------------------------------------------------------------------
# Step 4: Build + push Docker images
# ---------------------------------------------------------------------------
echo ""
echo "🐳 Step 4: Building and pushing Docker images..."
gcloud auth configure-docker "${AR_HOST}" --quiet

cd "${REPO_ROOT}"
for SVC in mcp-server job-api agent-runtime frontend-streamlit; do
  echo "  Building ${SVC}..."
  docker build -t "${AR_REPO}/${SVC}:latest" \
    -f "apps/${SVC}/Dockerfile" . \
    --build-arg BUILDKIT_INLINE_CACHE=1
  docker push "${AR_REPO}/${SVC}:latest"
  echo "  ✅ ${SVC} pushed"
done

# ---------------------------------------------------------------------------
# Step 5: Deploy application services to Cloud Run
# ---------------------------------------------------------------------------
echo ""
echo "🚀 Step 5: Deploying to Cloud Run..."

CF="--region=${REGION} --project=${PROJECT_ID} --allow-unauthenticated"

# --- MCP Server ---
# Teaching note: CpuAllocPerProjectRegion quota is 20,000 mCPU per project/region.
# All services combined must stay under that: 3+3+6+3+4 = 19,000 mCPU total.
gcloud run deploy mcp-server \
  --image="${AR_REPO}/mcp-server:latest" ${CF} \
  --service-account="mcp-server@${PROJECT_ID}.iam.gserviceaccount.com" \
  --set-env-vars="LLM_PROVIDER=vertex_ai,GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION},SERVICE_NAME=mcp-server,ENVIRONMENT=production,LOG_FORMAT=json" \
  --min-instances=0 --max-instances=3 --memory=512Mi --cpu=1

MCP_URL=$(gcloud run services describe mcp-server --region="${REGION}" \
  --project="${PROJECT_ID}" --format="value(status.url)")
echo "  ✅ MCP Server: ${MCP_URL}"

# --- Job API ---
# Teaching note: --network/--subnet/--vpc-egress (Direct VPC Egress, Cloud Run 2nd gen)
#   Memorystore Redis lives at a private VPC IP (10.x.x.x).  Without this, Cloud Run
#   has no route to the VPC and Redis TCP connections hang forever — breaking rate
#   limiting and idempotency. private-ranges-only routes only RFC-1918 traffic (Redis)
#   through the VPC; public traffic (Pub/Sub, Firestore, etc.) stays on the internet.
#   No VPC connector resource needed — Direct VPC Egress is built into Cloud Run 2nd gen.
gcloud run deploy job-api \
  --image="${AR_REPO}/job-api:latest" ${CF} \
  --service-account="job-api@${PROJECT_ID}.iam.gserviceaccount.com" \
  --set-env-vars="PUBSUB_PROJECT_ID=${PROJECT_ID},FIRESTORE_PROJECT_ID=${PROJECT_ID},REDIS_URL=${REDIS_URL},ENVIRONMENT=production,LOG_FORMAT=json" \
  --set-secrets="INTERNAL_API_TOKEN=internal-api-token:latest" \
  --network=stock-agent-vpc --subnet=stock-agent-subnet --vpc-egress=private-ranges-only \
  --min-instances=1 --max-instances=3 --memory=256Mi --cpu=1

JOB_API_URL=$(gcloud run services describe job-api --region="${REGION}" \
  --project="${PROJECT_ID}" --format="value(status.url)")
echo "  ✅ Job API: ${JOB_API_URL}"

# --- Agent Runtime (Cloud Run, HTTP fallback mode) ---
# Also needs VPC egress: agent-runtime caches LLM results in Redis (same 10.x.x.x host).
gcloud run deploy agent-runtime \
  --image="${AR_REPO}/agent-runtime:latest" ${CF} \
  --service-account="agent-runtime@${PROJECT_ID}.iam.gserviceaccount.com" \
  --set-env-vars="WORKER_MODE=http,LLM_PROVIDER=vertex_ai,MCP_SERVER_URL=${MCP_URL},JOB_API_URL=${JOB_API_URL},PUBSUB_PROJECT_ID=${PROJECT_ID},FIRESTORE_PROJECT_ID=${PROJECT_ID},REDIS_URL=${REDIS_URL},GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION},ENVIRONMENT=production,LOG_FORMAT=json,LANGFUSE_ENABLED=true,LANGFUSE_PUBLIC_KEY=${LF_PUBLIC_KEY}" \
  --set-secrets="INTERNAL_API_TOKEN=internal-api-token:latest,LANGFUSE_SECRET_KEY=langfuse-secret-key:latest" \
  --network=stock-agent-vpc --subnet=stock-agent-subnet --vpc-egress=private-ranges-only \
  --min-instances=1 --max-instances=3 --memory=2Gi --cpu=2 --timeout=600

AGENT_URL=$(gcloud run services describe agent-runtime --region="${REGION}" \
  --project="${PROJECT_ID}" --format="value(status.url)")
echo "  ✅ Agent Runtime (http): ${AGENT_URL}"

# Patch job-api with real agent URL
gcloud run services update job-api --region="${REGION}" --project="${PROJECT_ID}" \
  --update-env-vars="AGENT_RUNTIME_URL=${AGENT_URL}" --quiet

# --- Frontend ---
gcloud run deploy frontend-streamlit \
  --image="${AR_REPO}/frontend-streamlit:latest" ${CF} \
  --set-env-vars="JOB_API_URL=${JOB_API_URL},ENVIRONMENT=production" \
  --min-instances=0 --max-instances=3 --memory=256Mi --cpu=1

FRONTEND_URL=$(gcloud run services describe frontend-streamlit --region="${REGION}" \
  --project="${PROJECT_ID}" --format="value(status.url)")
echo "  ✅ Frontend: ${FRONTEND_URL}"

# ---------------------------------------------------------------------------
# Step 6: Deploy Langfuse to Cloud Run (backed by Cloud SQL)
# ---------------------------------------------------------------------------
echo ""
echo "📊 Step 6: Deploying Langfuse (LLM observability)..."
echo "   Teaching note: Cloud Run + Cloud SQL Proxy — no connection string in code."
echo "   The --add-cloudsql-instances flag injects a Unix socket the app connects to."
echo ""

# LF_ADMIN_PASS already read from Terraform output in Step 2 — nothing to do here.

# Deploy without NEXTAUTH_URL first (need URL to set URL — chicken/egg)
gcloud run deploy langfuse \
  --image=langfuse/langfuse:2 \
  ${CF} \
  --service-account="langfuse@${PROJECT_ID}.iam.gserviceaccount.com" \
  --add-cloudsql-instances="${LANGFUSE_DB_CONN}" \
  --set-secrets="DATABASE_URL=langfuse-db-url:latest" \
  --set-env-vars="\
NEXTAUTH_URL=https://placeholder.run.app,\
NEXTAUTH_SECRET=${LF_NEXTAUTH_SECRET},\
SALT=$(openssl rand -base64 16 | tr -dc 'A-Za-z0-9'),\
ENCRYPTION_KEY=${LF_ENCRYPTION_KEY},\
LANGFUSE_INIT_ORG_ID=prod-org,\
LANGFUSE_INIT_ORG_NAME=Stock Agent Demo,\
LANGFUSE_INIT_PROJECT_ID=prod-project,\
LANGFUSE_INIT_PROJECT_NAME=Stock Analysis,\
LANGFUSE_INIT_PROJECT_PUBLIC_KEY=${LF_PUBLIC_KEY},\
LANGFUSE_INIT_PROJECT_SECRET_KEY=${LF_SECRET_KEY},\
LANGFUSE_INIT_USER_EMAIL=admin@example.com,\
LANGFUSE_INIT_USER_NAME=Admin,\
LANGFUSE_INIT_USER_PASSWORD=${LF_ADMIN_PASS}" \
  --min-instances=0 --max-instances=2 \
  --memory=1Gi --cpu=2 --timeout=60

LANGFUSE_URL=$(gcloud run services describe langfuse --region="${REGION}" \
  --project="${PROJECT_ID}" --format="value(status.url)")

# Now update with the real URL (NextAuth needs it for redirects)
gcloud run services update langfuse --region="${REGION}" --project="${PROJECT_ID}" \
  --update-env-vars="NEXTAUTH_URL=${LANGFUSE_URL}" --quiet

echo "  ✅ Langfuse: ${LANGFUSE_URL}"
echo "     Login : admin@example.com / ${LF_ADMIN_PASS}"

# Update agent-runtime with Langfuse URL now that we have it
gcloud run services update agent-runtime --region="${REGION}" --project="${PROJECT_ID}" \
  --update-env-vars="LANGFUSE_HOST=${LANGFUSE_URL}" --quiet
echo "  ✅ agent-runtime updated with Langfuse URL"

# Update frontend so the sidebar "Langfuse LLM Traces" button points to the real URL.
# Teaching note: frontend is deployed before Langfuse (URL unknown then), so we patch
# it here after we have the URL — same pattern used for job-api ← AGENT_RUNTIME_URL.
gcloud run services update frontend-streamlit --region="${REGION}" --project="${PROJECT_ID}" \
  --update-env-vars="LANGFUSE_HOST=${LANGFUSE_URL}" --quiet
echo "  ✅ frontend updated with Langfuse URL"

# ---------------------------------------------------------------------------
# Step 7: Set up GKE — credentials, namespace, ConfigMap, K8s Secrets
# ---------------------------------------------------------------------------
echo ""
echo "☸️  Step 7: Setting up GKE..."

gcloud container clusters get-credentials agent-cluster \
  --region="${REGION}" --project="${PROJECT_ID}"
echo "  ✅ kubectl context → agent-cluster"

kubectl create namespace stock-agent --dry-run=client -o yaml | kubectl apply -f -

# ConfigMap with all non-sensitive config
kubectl create configmap stock-agent-config \
  --namespace=stock-agent \
  --from-literal=gcp_project="${PROJECT_ID}" \
  --from-literal=gcp_region="${REGION}" \
  --from-literal=mcp_server_url="${MCP_URL}" \
  --from-literal=job_api_url="${JOB_API_URL}" \
  --from-literal=redis_url="${REDIS_URL}" \
  --from-literal=langfuse_url="${LANGFUSE_URL}" \
  --from-literal=langfuse_public_key="${LF_PUBLIC_KEY}" \
  --dry-run=client -o yaml | kubectl apply -f -
echo "  ✅ ConfigMap ready"

# K8s Secret from Secret Manager values
# Teaching note: gemini_api_key is included so GKE pods can use google_ai_studio
# LLM provider (LLM_PROVIDER=google_ai_studio in deployment.yaml). This avoids
# the CrewAI + ChatVertexAI incompatibility — CrewAI's LiteLLM layer needs a
# model string, not a langchain object.
kubectl create secret generic stock-agent-secrets \
  --namespace=stock-agent \
  --from-literal=internal_api_token="${INTERNAL_TOKEN}" \
  --from-literal=langfuse_secret_key="${LF_SECRET_KEY}" \
  ${GEMINI_KEY_FOR_K8S:+--from-literal=gemini_api_key="${GEMINI_KEY_FOR_K8S}"} \
  --dry-run=client -o yaml | kubectl apply -f -
echo "  ✅ K8s Secret ready"

# ---------------------------------------------------------------------------
# Step 8: Deploy agent-runtime workers to GKE (Pub/Sub mode)
# ---------------------------------------------------------------------------
echo ""
echo "☸️  Step 8: Deploying GKE workers (pubsub mode)..."

export GCP_PROJECT="${PROJECT_ID}"
export GCP_REGION="${REGION}"
export MCP_URL
export JOB_API_URL
export REDIS_URL
export LANGFUSE_URL
export LANGFUSE_PUBLIC_KEY="${LF_PUBLIC_KEY}"

envsubst < "${REPO_ROOT}/infra/kubernetes/deployment.yaml" | kubectl apply -f -

echo "  Waiting for rollout (up to 5 min on GKE Autopilot first boot)..."
kubectl rollout status deployment/agent-runtime \
  --namespace=stock-agent --timeout=300s

PODS=$(kubectl get pods -n stock-agent -l app=agent-runtime \
  --no-headers 2>/dev/null | grep -c "Running" || echo "0")
echo "  ✅ ${PODS} agent-runtime pod(s) running"

# ---------------------------------------------------------------------------
# Done!
# ---------------------------------------------------------------------------
echo ""
echo "============================================================"
echo "  ✅ Lab 4 Complete!"
echo "============================================================"
echo ""
echo "  Application:"
echo "    🌐 Frontend     : ${FRONTEND_URL}"
echo "    📚 Job API docs : ${JOB_API_URL}/docs"
echo "    🔧 MCP Server   : ${MCP_URL}/docs"
echo ""
echo "  Observability:"
echo "    📊 Langfuse     : ${LANGFUSE_URL}"
echo "       Login        : admin@example.com / ${LF_ADMIN_PASS}"
echo ""
echo "  GKE workers:"
echo "    kubectl get pods -n stock-agent"
echo "    kubectl logs -n stock-agent -l app=agent-runtime -f"
echo ""
echo "  GCP console:"
echo "    📋 Cloud Logging : https://console.cloud.google.com/logs?project=${PROJECT_ID}"
echo "    🔍 Cloud Trace   : https://console.cloud.google.com/traces?project=${PROJECT_ID}"
echo ""
echo "  💡 Stop billing: make teardown GCP_PROJECT=${PROJECT_ID}"
echo ""
