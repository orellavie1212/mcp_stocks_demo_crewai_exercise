#!/usr/bin/env bash
# =============================================================================
# deploy-cloud-run.sh — Deploy all services to Cloud Run
# =============================================================================
# Called by: make deploy-run (build + push + this script)
# Pre-requisites:
#   - Images already built and pushed to Artifact Registry
#   - Terraform infrastructure already provisioned (make infra-up)
#
# Teaching note:
#   This script is idempotent — running it again just redeploys (rolling update).
#   Cloud Run keeps the previous revision available during rollout.
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
  PROJECT_ID=$(gcloud config get-value project 2>/dev/null || echo "")
  if [[ -z "$PROJECT_ID" ]]; then
    echo "❌ --project is required"
    exit 1
  fi
fi

AR_HOST="${REGION}-docker.pkg.dev"
AR_REPO="${AR_HOST}/${PROJECT_ID}/stock-agent"
COMMON_FLAGS="--region=${REGION} --project=${PROJECT_ID} --allow-unauthenticated"

# ---------------------------------------------------------------------------
# Read infra values from Terraform outputs
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/../infra/terraform"

REDIS_HOST=$(terraform output -raw redis_host 2>/dev/null || echo "")
REDIS_PORT=$(terraform output -raw redis_port 2>/dev/null || echo "6379")

if [[ -z "$REDIS_HOST" ]]; then
  echo "❌ Could not read redis_host from Terraform outputs."
  echo "   Run 'make infra-up GCP_PROJECT=${PROJECT_ID}' first."
  exit 1
fi

cd "${SCRIPT_DIR}/.."

echo ""
echo "🚀 Deploying to Cloud Run"
echo "   Project : $PROJECT_ID"
echo "   Region  : $REGION"
echo "   Redis   : $REDIS_HOST:$REDIS_PORT"
echo ""

# ---------------------------------------------------------------------------
# 1. MCP Server
# ---------------------------------------------------------------------------
echo "  Deploying mcp-server..."
gcloud run deploy mcp-server \
  --image="${AR_REPO}/mcp-server:latest" \
  ${COMMON_FLAGS} \
  --service-account="mcp-server@${PROJECT_ID}.iam.gserviceaccount.com" \
  --set-env-vars="\
LLM_PROVIDER=vertex_ai,\
GCP_PROJECT=${PROJECT_ID},\
GCP_REGION=${REGION},\
SERVICE_NAME=mcp-server,\
ENVIRONMENT=production,\
LOG_FORMAT=json" \
  --min-instances=0 --max-instances=10 \
  --memory=512Mi --cpu=1

MCP_URL=$(gcloud run services describe mcp-server \
  --region="${REGION}" --project="${PROJECT_ID}" \
  --format="value(status.url)")
echo "  ✅ MCP Server: ${MCP_URL}"

# ---------------------------------------------------------------------------
# 2. Job API
# ---------------------------------------------------------------------------
echo ""
echo "  Deploying job-api..."
gcloud run deploy job-api \
  --image="${AR_REPO}/job-api:latest" \
  ${COMMON_FLAGS} \
  --service-account="job-api@${PROJECT_ID}.iam.gserviceaccount.com" \
  --set-env-vars="\
PUBSUB_PROJECT_ID=${PROJECT_ID},\
FIRESTORE_PROJECT_ID=${PROJECT_ID},\
REDIS_URL=redis://${REDIS_HOST}:${REDIS_PORT}/0,\
AGENT_RUNTIME_URL=http://agent-runtime-placeholder,\
ENVIRONMENT=production,\
LOG_FORMAT=json" \
  --set-secrets="INTERNAL_API_TOKEN=internal-api-token:latest" \
  --min-instances=0 --max-instances=10 \
  --memory=256Mi --cpu=1

JOB_API_URL=$(gcloud run services describe job-api \
  --region="${REGION}" --project="${PROJECT_ID}" \
  --format="value(status.url)")
echo "  ✅ Job API: ${JOB_API_URL}"

# ---------------------------------------------------------------------------
# 3. Agent Runtime (HTTP fallback mode on Cloud Run)
#    Teaching note: In Lab 4, the real workers run on GKE in pubsub mode.
#    This Cloud Run deployment provides an HTTP fallback for job-api.
# ---------------------------------------------------------------------------
echo ""
echo "  Deploying agent-runtime (http fallback)..."
gcloud run deploy agent-runtime \
  --image="${AR_REPO}/agent-runtime:latest" \
  ${COMMON_FLAGS} \
  --service-account="agent-runtime@${PROJECT_ID}.iam.gserviceaccount.com" \
  --set-env-vars="\
WORKER_MODE=http,\
LLM_PROVIDER=vertex_ai,\
MCP_SERVER_URL=${MCP_URL},\
JOB_API_URL=${JOB_API_URL},\
PUBSUB_PROJECT_ID=${PROJECT_ID},\
FIRESTORE_PROJECT_ID=${PROJECT_ID},\
REDIS_URL=redis://${REDIS_HOST}:${REDIS_PORT}/0,\
GCP_PROJECT=${PROJECT_ID},\
GCP_REGION=${REGION},\
ENVIRONMENT=production,\
LOG_FORMAT=json" \
  --set-secrets="\
INTERNAL_API_TOKEN=internal-api-token:latest,\
LANGFUSE_SECRET_KEY=langfuse-secret-key:latest" \
  --min-instances=1 --max-instances=20 \
  --memory=2Gi --cpu=2 \
  --timeout=600

AGENT_URL=$(gcloud run services describe agent-runtime \
  --region="${REGION}" --project="${PROJECT_ID}" \
  --format="value(status.url)")
echo "  ✅ Agent Runtime (http): ${AGENT_URL}"

# Update job-api with the real agent runtime URL now that we have it
echo ""
echo "  Updating job-api with real agent-runtime URL..."
gcloud run services update job-api \
  --region="${REGION}" --project="${PROJECT_ID}" \
  --update-env-vars="AGENT_RUNTIME_URL=${AGENT_URL}" \
  --quiet
echo "  ✅ job-api updated"

# ---------------------------------------------------------------------------
# 4. Frontend Streamlit
# ---------------------------------------------------------------------------
echo ""
echo "  Deploying frontend-streamlit..."
gcloud run deploy frontend-streamlit \
  --image="${AR_REPO}/frontend-streamlit:latest" \
  ${COMMON_FLAGS} \
  --set-env-vars="\
JOB_API_URL=${JOB_API_URL},\
ENVIRONMENT=production" \
  --min-instances=0 --max-instances=5 \
  --memory=256Mi --cpu=1

FRONTEND_URL=$(gcloud run services describe frontend-streamlit \
  --region="${REGION}" --project="${PROJECT_ID}" \
  --format="value(status.url)")
echo "  ✅ Frontend: ${FRONTEND_URL}"

# ---------------------------------------------------------------------------
# Export URLs for GKE setup (if running inside setup-gcp.sh)
# ---------------------------------------------------------------------------
export MCP_URL JOB_API_URL AGENT_URL FRONTEND_URL REDIS_HOST REDIS_PORT

echo ""
echo "============================================================"
echo "  ✅ Cloud Run deployment complete!"
echo "============================================================"
echo ""
echo "  🌐 Frontend     : ${FRONTEND_URL}"
echo "  📚 Job API docs : ${JOB_API_URL}/docs"
echo "  🔧 MCP Server   : ${MCP_URL}/docs"
echo ""
echo "  Next: deploy GKE workers (pubsub mode):"
echo "     make deploy-gke GCP_PROJECT=${PROJECT_ID} GCP_REGION=${REGION}"
echo ""
