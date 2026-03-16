#!/usr/bin/env bash
# =============================================================================
# teardown-gcp.sh — Complete GCP teardown (delete ALL resources, start from zero)
# =============================================================================
# Usage:
#   ./scripts/teardown-gcp.sh --project=my-project-id [--region=us-central1] [--yes]
#
# What this DELETES:
#   - Cloud Run services (mcp-server, job-api, agent-runtime, frontend-streamlit)
#   - GKE Autopilot cluster (agent-cluster) + all workloads
#   - Memorystore Redis (stock-agent-cache)
#   - Cloud SQL PostgreSQL (langfuse-db)
#   - VPC network (stock-agent-vpc)
#   - Pub/Sub topics + subscriptions
#   - Artifact Registry images + repository
#   - Secret Manager secrets
#   - Service accounts + IAM bindings
#   - Firestore database
#   - Terraform state is left clean (empty) so next 'terraform apply' is fresh
#
# What is KEPT:
#   - GCP project itself
#   - Enabled APIs (no cost, they stay enabled)
#   - Local .env file
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
PROJECT_ID=""
REGION="us-central1"
SKIP_CONFIRM=false

for arg in "$@"; do
  case $arg in
    --project=*) PROJECT_ID="${arg#*=}" ;;
    --region=*)  REGION="${arg#*=}" ;;
    --yes)       SKIP_CONFIRM=true ;;
    *) echo "Unknown argument: $arg" && exit 1 ;;
  esac
done

if [[ -z "$PROJECT_ID" ]]; then
  PROJECT_ID=$(gcloud config get-value project 2>/dev/null || echo "")
  if [[ -z "$PROJECT_ID" ]]; then
    echo "❌ --project is required"
    echo "Usage: ./scripts/teardown-gcp.sh --project=my-project-id"
    exit 1
  fi
fi

AR_HOST="${REGION}-docker.pkg.dev"

echo ""
echo "============================================================"
echo "  ⚠️  Stock Agent Platform — FULL GCP TEARDOWN"
echo "============================================================"
echo "  Project : $PROJECT_ID"
echo "  Region  : $REGION"
echo ""
echo "  This will permanently DELETE:"
echo "    ✗ Cloud Run services (5: mcp-server, job-api, agent-runtime, frontend-streamlit, langfuse)"
echo "    ✗ GKE Autopilot cluster + all pods"
echo "    ✗ Memorystore Redis"
echo "    ✗ Cloud SQL (langfuse-db-* auto-named by Terraform)"
echo "    ✗ VPC network + subnets"
echo "    ✗ Pub/Sub topics + subscriptions"
echo "    ✗ Artifact Registry repository + all images"
echo "    ✗ Secret Manager secrets"
echo "    ✗ Service accounts + IAM bindings"
echo "    ✗ Firestore database"
echo ""
echo "  After this, run: make setup-gcp GCP_PROJECT=$PROJECT_ID"
echo "============================================================"
echo ""

if [[ "$SKIP_CONFIRM" != "true" ]]; then
  read -p "  Type the project ID to confirm: " confirm
  if [[ "$confirm" != "$PROJECT_ID" ]]; then
    echo ""
    echo "❌ Confirmation failed. Aborted."
    exit 1
  fi
fi

echo ""
echo "Starting teardown..."

# ---------------------------------------------------------------------------
# Step 1: Delete Cloud Run services
# Teaching note:
#   --async    = don't block waiting 2-3 min per service (fire and move on)
#   No describe check = no extra API call that can hang on fresh projects
#   || true    = silently skip if service doesn't exist (NOT_FOUND is fine)
# ---------------------------------------------------------------------------
echo ""
echo "🗑️  Step 1: Deleting Cloud Run services (async, no existence check)..."

for SVC in mcp-server job-api agent-runtime frontend-streamlit langfuse; do
  gcloud run services delete "$SVC" \
    --region="$REGION" --project="$PROJECT_ID" \
    --quiet --async 2>/dev/null \
    && echo "  🔄 Deletion triggered: $SVC" \
    || echo "  ⏭️  Not found (skipping): $SVC"
done
echo "  ✅ Done — continuing (deletions complete in background)"

# ---------------------------------------------------------------------------
# Step 2: Delete GKE workloads (namespace)
# Terraform destroy will delete the cluster itself
# No describe check — just try get-credentials and ignore failure
# ---------------------------------------------------------------------------
echo ""
echo "🗑️  Step 2: Cleaning up GKE namespace (if cluster exists)..."
if gcloud container clusters get-credentials agent-cluster \
    --region="$REGION" --project="$PROJECT_ID" --quiet 2>/dev/null; then
  kubectl delete namespace stock-agent --ignore-not-found=true 2>/dev/null || true
  echo "  ✅ Namespace 'stock-agent' deleted (cluster destroyed by Terraform)"
else
  echo "  ⏭️  GKE cluster not found or inaccessible (skipping)"
fi

# ---------------------------------------------------------------------------
# Step 3: Delete Artifact Registry images
# Terraform can't destroy a non-empty registry — must empty it first
# ---------------------------------------------------------------------------
echo ""
echo "🗑️  Step 3: Deleting Artifact Registry images (no list check)..."
for SVC in mcp-server job-api agent-runtime frontend-streamlit; do
  IMG="${AR_HOST}/${PROJECT_ID}/stock-agent/${SVC}"
  gcloud artifacts docker images delete "$IMG" \
    --project="$PROJECT_ID" --quiet --delete-tags 2>/dev/null \
    && echo "  ✅ Deleted images: $SVC" \
    || echo "  ⏭️  No images found (skipping): $SVC"
done

# ---------------------------------------------------------------------------
# Step 4: Full Terraform destroy
# This removes: GKE, Redis, Cloud SQL, VPC, Pub/Sub, IAM, Secrets, AR repo
#
# Teaching note: Why terraform state rm before destroy?
#   google_project_service resources have disable_on_destroy=true in the
#   EXISTING state (set when they were first created). Terraform uses the
#   STATE value during destroy, not the new config. Running destroy directly
#   always fails with "service has dependent services" errors.
#
#   Solution: evict API service records from state before destroy.
#   terraform state rm forgets them WITHOUT disabling the real GCP APIs.
#   The actual infrastructure (GKE, Redis, SQL, VPC...) stays in state
#   and gets properly destroyed. APIs cost $0 and are re-imported by the
#   next terraform apply.
# ---------------------------------------------------------------------------
echo ""
echo "🗑️  Step 4: Running terraform destroy..."
echo "   (GKE ~5min, Cloud SQL ~3min, Redis ~2min, VPC ~1min)"
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/../infra/terraform"

# Init providers (non-interactive, no backend reconfiguration)
echo "  Initializing Terraform..."
terraform init -input=false 2>&1 | grep -E "(Initializing|Installed|Warning|Error)" || true

# ---------------------------------------------------------------------------
# Teaching note: Why we remove google_project_service from state before destroy.
#
# These resources were created when disable_on_destroy defaulted to true in
# the Terraform Google provider.  Terraform reads the STATE value (true) at
# destroy time — not the new config value (false) — so it always tries to
# disable the APIs, which GCP refuses because logging/pubsub/iam/monitoring
# have system-level dependents.
#
# Fix: evict them from state before destroy.  "terraform state rm" removes
# the record WITHOUT touching the real GCP resource. APIs stay enabled ($0),
# and terraform destroy only sees the real infrastructure (GKE, Redis, SQL…).
# ---------------------------------------------------------------------------
echo "  Evicting google_project_service resources from state (keeps APIs enabled)..."
# Use variable capture, not a pipeline, so set -o pipefail cannot kill the script
APIS_IN_STATE=$(terraform state list 2>/dev/null | grep "^google_project_service" || true)
if [[ -n "$APIS_IN_STATE" ]]; then
  while IFS= read -r resource; do
    terraform state rm "$resource" 2>/dev/null \
      && echo "    ✅ removed from state: $resource" \
      || echo "    ⏭️  already absent: $resource"
  done <<< "$APIS_IN_STATE"
  echo "  ✅ API service records removed — will NOT be disabled"
else
  echo "  ⏭️  No google_project_service resources in state (skipping)"
fi

terraform destroy -auto-approve \
  -var="project_id=${PROJECT_ID}" \
  -var="region=${REGION}" \
  2>&1

cd "${SCRIPT_DIR}/.."

# ---------------------------------------------------------------------------
# Done!
# ---------------------------------------------------------------------------
echo ""
echo "============================================================"
echo "  ✅ Full teardown complete!"
echo "============================================================"
echo ""
echo "  Terraform state is now clean (empty)."
echo "  All GCP resources have been deleted."
echo ""
echo "  To deploy from scratch (Lab 4):"
echo ""
echo "     make setup-gcp GCP_PROJECT=${PROJECT_ID} GCP_REGION=${REGION}"
echo ""
echo "  ⚠️  Remember to add your Gemini API key after setup:"
echo "     echo -n 'your-key' | gcloud secrets versions add gemini-api-key \\"
echo "       --data-file=- --project=${PROJECT_ID}"
echo "     # Or use Vertex AI (no key needed): LLM_PROVIDER=vertex_ai"
echo ""
