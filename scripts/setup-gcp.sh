#!/usr/bin/env bash
# =============================================================================
# setup-gcp.sh — One-command GCP project setup
# =============================================================================
# Usage:
#   ./scripts/setup-gcp.sh --project=my-stock-agent-123 --region=us-central1
#
# Prerequisites:
#   - gcloud CLI installed and authenticated (gcloud auth login)
#   - Terraform >= 1.6 installed
#   - Docker installed
#   - $300 GCP free trial credit (or billing enabled)
#
# What this script does:
#   1. Sets the GCP project
#   2. Provisions all infrastructure with Terraform
#   3. Builds + pushes Docker images to Artifact Registry
#   4. Deploys all services to Cloud Run
#   5. Seeds Secret Manager with initial secrets
#   6. Prints all service URLs
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
PROJECT_ID=""
REGION="us-central1"

for arg in "$@"; do
  case $arg in
    --project=*) PROJECT_ID="${arg#*=}" ;;
    --region=*)  REGION="${arg#*=}" ;;
    *) echo "Unknown argument: $arg" && exit 1 ;;
  esac
done

if [[ -z "$PROJECT_ID" ]]; then
  # Try to detect from gcloud config
  PROJECT_ID=$(gcloud config get-value project 2>/dev/null || echo "")
  if [[ -z "$PROJECT_ID" ]]; then
    echo "❌ Error: --project is required"
    echo "Usage: ./scripts/setup-gcp.sh --project=my-project-id"
    exit 1
  fi
fi

AR_HOST="${REGION}-docker.pkg.dev"
AR_REPO="${AR_HOST}/${PROJECT_ID}/stock-agent"

echo ""
echo "============================================================"
echo "  Stock Agent Platform — GCP Setup"
echo "============================================================"
echo "  Project:  $PROJECT_ID"
echo "  Region:   $REGION"
echo "  Registry: $AR_REPO"
echo "============================================================"
echo ""

# ---------------------------------------------------------------------------
# Step 1: Set GCP project
# ---------------------------------------------------------------------------
echo "📋 Step 1: Configuring GCP project..."
gcloud config set project "$PROJECT_ID"
gcloud config set compute/region "$REGION"

# ---------------------------------------------------------------------------
# Step 2: Terraform — provision infrastructure
# ---------------------------------------------------------------------------
echo ""
echo "🏗️  Step 2: Provisioning GCP infrastructure with Terraform..."
echo "   This takes ~5 minutes on first run."
echo ""

cd infra/terraform
terraform init -upgrade
terraform apply -auto-approve \
  -var="project_id=${PROJECT_ID}" \
  -var="region=${REGION}"

REDIS_HOST=$(terraform output -raw redis_host)
REDIS_PORT=$(terraform output -raw redis_port)
cd ../..

echo "✅ Infrastructure provisioned!"
echo "   Redis: ${REDIS_HOST}:${REDIS_PORT}"

# ---------------------------------------------------------------------------
# Step 3: Seed Secret Manager
# ---------------------------------------------------------------------------
echo ""
echo "🔐 Step 3: Setting up secrets in Secret Manager..."

# Internal API token
INTERNAL_TOKEN=$(openssl rand -base64 32)
echo -n "$INTERNAL_TOKEN" | gcloud secrets versions add internal-api-token \
  --data-file=- --project="$PROJECT_ID" 2>/dev/null || \
echo -n "$INTERNAL_TOKEN" | gcloud secrets create internal-api-token \
  --data-file=- --project="$PROJECT_ID"

# Langfuse secret key
LANGFUSE_KEY=$(openssl rand -base64 32)
echo -n "$LANGFUSE_KEY" | gcloud secrets versions add langfuse-secret-key \
  --data-file=- --project="$PROJECT_ID" 2>/dev/null || \
echo -n "$LANGFUSE_KEY" | gcloud secrets create langfuse-secret-key \
  --data-file=- --project="$PROJECT_ID"

echo "✅ Secrets created in Secret Manager"
echo ""
echo "⚠️  IMPORTANT: Add your Gemini API key to Secret Manager:"
echo "   gcloud secrets versions add gemini-api-key --data-file=- <<< 'your-key'"
echo "   (Or use Vertex AI — no key needed if LLM_PROVIDER=vertex_ai)"

# ---------------------------------------------------------------------------
# Step 4: Build and push Docker images
# ---------------------------------------------------------------------------
echo ""
echo "🐳 Step 4: Building and pushing Docker images..."
gcloud auth configure-docker "${AR_HOST}" --quiet

for SVC in mcp-server job-api agent-runtime frontend-streamlit; do
  echo "  Building ${SVC}..."
  docker build -t "${AR_REPO}/${SVC}:latest" \
    -f "apps/${SVC}/Dockerfile" . \
    --build-arg BUILDKIT_INLINE_CACHE=1
  docker push "${AR_REPO}/${SVC}:latest"
  echo "  ✅ ${SVC} pushed"
done

# ---------------------------------------------------------------------------
# Step 5: Deploy to Cloud Run
# ---------------------------------------------------------------------------
echo ""
echo "🚀 Step 5: Deploying services to Cloud Run..."

# Common Cloud Run flags
COMMON_FLAGS="--region=${REGION} --project=${PROJECT_ID} --allow-unauthenticated"

# MCP Server
gcloud run deploy mcp-server \
  --image="${AR_REPO}/mcp-server:latest" \
  ${COMMON_FLAGS} \
  --service-account="mcp-server@${PROJECT_ID}.iam.gserviceaccount.com" \
  --set-env-vars="LLM_PROVIDER=vertex_ai,GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION},SERVICE_NAME=mcp-server,ENVIRONMENT=production,LOG_FORMAT=json" \
  --min-instances=0 --max-instances=10 \
  --memory=512Mi --cpu=1

MCP_URL=$(gcloud run services describe mcp-server --region="${REGION}" --format="value(status.url)")
echo "  ✅ MCP Server: ${MCP_URL}"

# Job API
gcloud run deploy job-api \
  --image="${AR_REPO}/job-api:latest" \
  ${COMMON_FLAGS} \
  --service-account="job-api@${PROJECT_ID}.iam.gserviceaccount.com" \
  --set-env-vars="PUBSUB_PROJECT_ID=${PROJECT_ID},FIRESTORE_PROJECT_ID=${PROJECT_ID},REDIS_URL=redis://${REDIS_HOST}:${REDIS_PORT}/0,ENVIRONMENT=production,LOG_FORMAT=json" \
  --set-secrets="INTERNAL_API_TOKEN=internal-api-token:latest" \
  --min-instances=0 --max-instances=10 \
  --memory=256Mi --cpu=1

JOB_API_URL=$(gcloud run services describe job-api --region="${REGION}" --format="value(status.url)")
echo "  ✅ Job API: ${JOB_API_URL}"

# Agent Runtime (as Cloud Run Job for long-running workers, or deploy as service)
gcloud run deploy agent-runtime \
  --image="${AR_REPO}/agent-runtime:latest" \
  ${COMMON_FLAGS} \
  --service-account="agent-runtime@${PROJECT_ID}.iam.gserviceaccount.com" \
  --set-env-vars="WORKER_MODE=http,LLM_PROVIDER=vertex_ai,MCP_SERVER_URL=${MCP_URL},JOB_API_URL=${JOB_API_URL},PUBSUB_PROJECT_ID=${PROJECT_ID},FIRESTORE_PROJECT_ID=${PROJECT_ID},REDIS_URL=redis://${REDIS_HOST}:${REDIS_PORT}/0,GCP_PROJECT=${PROJECT_ID},GCP_REGION=${REGION},ENVIRONMENT=production,LOG_FORMAT=json" \
  --set-secrets="INTERNAL_API_TOKEN=internal-api-token:latest,LANGFUSE_SECRET_KEY=langfuse-secret-key:latest" \
  --min-instances=1 --max-instances=20 \
  --memory=2Gi --cpu=2 \
  --timeout=600

AGENT_URL=$(gcloud run services describe agent-runtime --region="${REGION}" --format="value(status.url)")
echo "  ✅ Agent Runtime: ${AGENT_URL}"

# Frontend
gcloud run deploy frontend-streamlit \
  --image="${AR_REPO}/frontend-streamlit:latest" \
  ${COMMON_FLAGS} \
  --set-env-vars="JOB_API_URL=${JOB_API_URL},ENVIRONMENT=production" \
  --min-instances=0 --max-instances=5 \
  --memory=256Mi --cpu=1

FRONTEND_URL=$(gcloud run services describe frontend-streamlit --region="${REGION}" --format="value(status.url)")
echo "  ✅ Frontend: ${FRONTEND_URL}"

# ---------------------------------------------------------------------------
# Done!
# ---------------------------------------------------------------------------
echo ""
echo "============================================================"
echo "  ✅ Deployment Complete!"
echo "============================================================"
echo ""
echo "  🌐 Streamlit UI   : ${FRONTEND_URL}"
echo "  📚 Job API Docs   : ${JOB_API_URL}/docs"
echo "  🔧 MCP Server     : ${MCP_URL}/docs"
echo ""
echo "  📊 Cloud Logging  : https://console.cloud.google.com/logs?project=${PROJECT_ID}"
echo "  🔍 Cloud Trace    : https://console.cloud.google.com/traces?project=${PROJECT_ID}"
echo "  💰 Billing        : https://console.cloud.google.com/billing"
echo ""
echo "  💡 To stop billing when not teaching:"
echo "     make infra-down  (destroys GKE + Redis + Cloud SQL)"
echo "     Cloud Run scales to 0 automatically when idle"
echo ""
echo "  💡 Next step — add your Gemini API key (if using google_ai_studio):"
echo "     echo -n 'your-key' | gcloud secrets versions add gemini-api-key --data-file=-"
echo "     # Or use Vertex AI (no key needed on GCP): LLM_PROVIDER=vertex_ai"
echo ""
