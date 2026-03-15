# =============================================================================
# Terraform — GCP Infrastructure for Stock Agent Platform
# =============================================================================
# Teaching note:
#   Infrastructure as Code (IaC) makes deployments reproducible.
#   "The cluster that takes 15 minutes to click together in the console
#   takes 2 minutes to provision with terraform apply."
#
# Prerequisites:
#   terraform init
#   terraform apply -var="project_id=your-project" -var="region=us-central1"
# =============================================================================

terraform {
  required_version = ">= 1.6"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# ---------------------------------------------------------------------------
# Variables
# ---------------------------------------------------------------------------

variable "project_id" {
  description = "GCP Project ID (get from: gcloud config get-value project)"
  type        = string
}

variable "region" {
  description = "GCP region"
  type        = string
  default     = "us-central1"
}

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "production"
}

# ---------------------------------------------------------------------------
# Enable required APIs
# ---------------------------------------------------------------------------

resource "google_project_service" "apis" {
  for_each = toset([
    "run.googleapis.com",              # Cloud Run
    "container.googleapis.com",        # GKE
    "pubsub.googleapis.com",           # Pub/Sub
    "firestore.googleapis.com",        # Firestore
    "redis.googleapis.com",            # Memorystore Redis
    "sqladmin.googleapis.com",         # Cloud SQL (for Langfuse)
    "secretmanager.googleapis.com",    # Secret Manager
    "artifactregistry.googleapis.com", # Artifact Registry
    "cloudbuild.googleapis.com",       # Cloud Build
    "monitoring.googleapis.com",       # Cloud Monitoring
    "logging.googleapis.com",          # Cloud Logging
    "cloudtrace.googleapis.com",       # Cloud Trace
    "iam.googleapis.com",              # IAM
    "aiplatform.googleapis.com",       # Vertex AI (Gemini)
  ])
  service = each.value

  # Teaching note: NEVER disable APIs on teardown.
  # Core GCP APIs (logging, pubsub, iam, monitoring) have system-level dependents
  # that cannot be disabled — terraform destroy would always fail with dependency errors.
  # APIs cost $0 to keep enabled; just remove them from Terraform state on destroy.
  disable_on_destroy         = false
  disable_dependent_services = false
}

# ---------------------------------------------------------------------------
# Artifact Registry (Docker images)
# ---------------------------------------------------------------------------

resource "google_artifact_registry_repository" "stock_agent" {
  location      = var.region
  repository_id = "stock-agent"
  format        = "DOCKER"
  description   = "Stock Agent Platform Docker images"
  depends_on    = [google_project_service.apis]
}

# ---------------------------------------------------------------------------
# Pub/Sub (async message queue)
# Teaching note: Topics are like Kafka topics — named channels for messages
# ---------------------------------------------------------------------------

resource "google_pubsub_topic" "analysis_requests" {
  name = "analysis-requests"
  message_retention_duration = "86600s"  # 24h
  depends_on = [google_project_service.apis]
}

resource "google_pubsub_topic" "analysis_dlq" {
  name = "analysis-dlq"  # Dead letter queue for failed messages
  message_retention_duration = "604800s"  # 7 days — inspect failed jobs
  depends_on = [google_project_service.apis]
}

resource "google_pubsub_subscription" "agent_worker" {
  name  = "agent-worker-sub"
  topic = google_pubsub_topic.analysis_requests.name

  # Pub/Sub holds message until worker acks (max 10 minutes)
  ack_deadline_seconds = 600

  # Retry policy: exponential backoff 10s → 600s
  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }

  # Dead letter after 5 failed attempts (GCP minimum is 5)
  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.analysis_dlq.id
    max_delivery_attempts = 5
  }

  depends_on = [google_project_service.apis]
}

# ---------------------------------------------------------------------------
# Firestore (job state storage)
# Teaching note: Serverless, no instance to manage, real-time listeners built-in
# ---------------------------------------------------------------------------

# Teaching note: import block (Terraform ≥ 1.5) makes setup idempotent.
# GCP forbids re-creating the (default) Firestore database for up to 7 days after
# deletion.  On re-runs after a partial teardown the DB already exists → 409 on apply.
# This import block tells Terraform: "if this DB already exists in GCP, adopt it
# into state rather than trying to create it."  It is a no-op when the resource is
# already in state (safe to run every time).
import {
  to = google_firestore_database.default
  id = "projects/${var.project_id}/databases/(default)"
}

resource "google_firestore_database" "default" {
  project     = var.project_id
  name        = "(default)"
  location_id = var.region
  type        = "FIRESTORE_NATIVE"
  depends_on  = [google_project_service.apis]
}

# ---------------------------------------------------------------------------
# Memorystore Redis (caching + rate limiting)
# Teaching note: Managed Redis — no cluster to operate, auto-failover
# ---------------------------------------------------------------------------

resource "google_redis_instance" "cache" {
  name           = "stock-agent-cache"
  tier           = "BASIC"   # No replicas for demo (use STANDARD_HA for production)
  memory_size_gb = 1
  region         = var.region
  redis_version  = "REDIS_7_0"
  display_name   = "Stock Agent Cache"
  depends_on     = [google_project_service.apis]
}

# ---------------------------------------------------------------------------
# Cloud SQL (PostgreSQL for Langfuse)
# ---------------------------------------------------------------------------

# Teaching note: GCP locks deleted Cloud SQL instance names for 7 days.
# random_id (no keepers) regenerates each time Terraform starts from a clean/empty
# state — i.e. after every teardown.  This means every setup cycle gets a unique
# name like "langfuse-db-a3f1c2b4" automatically.  No manual suffix bumping needed.
resource "random_id" "sql_instance_suffix" {
  byte_length = 4
}

resource "google_sql_database_instance" "langfuse" {
  name             = "langfuse-db-${random_id.sql_instance_suffix.hex}"
  database_version = "POSTGRES_16"
  region           = var.region
  deletion_protection = false  # Set to true in real production

  settings {
    tier = "db-f1-micro"  # Cheapest tier — sufficient for demo
    disk_size = 10

    backup_configuration {
      enabled = true
    }

    ip_configuration {
      # Public IP — acceptable for demo; use private IP + Service Networking in production
      ipv4_enabled    = true
    }
  }
  lifecycle {
    # Ignore settings version mismatch on imported instances
    ignore_changes = [settings]
  }
  depends_on = [google_project_service.apis]
}

resource "google_sql_database" "langfuse" {
  name     = "langfuse"
  instance = google_sql_database_instance.langfuse.name
  # Teaching note: ABANDON = don't try to DROP the database on destroy.
  # The instance deletion cascades and removes everything. Trying to DROP the
  # database explicitly first always races with Langfuse still holding connections.
  deletion_policy = "ABANDON"
}

resource "google_sql_user" "langfuse" {
  name     = "langfuse"
  instance = google_sql_database_instance.langfuse.name
  password = random_password.langfuse_db.result
  # Teaching note: ABANDON = don't try to DROP ROLE on destroy.
  # Langfuse creates 54+ schema objects owned by this role; PostgreSQL refuses
  # to drop a role that owns objects (Error 400). Since we're deleting the entire
  # instance anyway, abandoning the user in state is correct and clean.
  deletion_policy = "ABANDON"
}

resource "random_password" "langfuse_db" {
  length  = 32
  special = false
}

# ---------------------------------------------------------------------------
# VPC (private networking)
# ---------------------------------------------------------------------------

resource "google_compute_network" "vpc" {
  name                    = "stock-agent-vpc"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.apis]
}

resource "google_compute_subnetwork" "subnet" {
  name          = "stock-agent-subnet"
  ip_cidr_range = "10.0.0.0/24"
  region        = var.region
  network       = google_compute_network.vpc.id

  secondary_ip_range {
    range_name    = "gke-pods"
    ip_cidr_range = "10.1.0.0/16"
  }

  secondary_ip_range {
    range_name    = "gke-services"
    ip_cidr_range = "10.2.0.0/20"
  }
}

# ---------------------------------------------------------------------------
# GKE Autopilot (agent workers)
# Teaching note: Autopilot = serverless GKE. Google manages nodes.
# You pay per pod, not per node. Scale to zero when not in use.
# ---------------------------------------------------------------------------

resource "google_container_cluster" "agent_cluster" {
  name     = "agent-cluster"
  location = var.region

  # Autopilot mode — Google manages all nodes
  enable_autopilot     = true
  deletion_protection  = false  # Allow terraform destroy

  network    = google_compute_network.vpc.name
  subnetwork = google_compute_subnetwork.subnet.name

  ip_allocation_policy {
    cluster_secondary_range_name  = "gke-pods"
    services_secondary_range_name = "gke-services"
  }

  # Workload Identity — pods authenticate as service accounts
  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  depends_on = [google_project_service.apis]
}

# ---------------------------------------------------------------------------
# Service Accounts (least-privilege IAM)
# Teaching note: Each service has its own identity with only the permissions it needs
# ---------------------------------------------------------------------------

resource "google_service_account" "mcp_server" {
  account_id   = "mcp-server"
  display_name = "MCP Server"
  description  = "MCP Server service account — reads Vertex AI models"
}

resource "google_service_account" "job_api" {
  account_id   = "job-api"
  display_name = "Job API"
  description  = "Job API — writes to Firestore, publishes to Pub/Sub"
}

resource "google_service_account" "agent_runtime" {
  account_id   = "agent-runtime"
  display_name = "Agent Runtime"
  description  = "Agent Runtime — reads from Pub/Sub, writes to Firestore, calls Vertex AI"
}

resource "google_service_account" "frontend" {
  account_id   = "frontend-streamlit"
  display_name = "Frontend Streamlit"
  description  = "Frontend — calls Job API only"
}

# Langfuse service account — only needs Cloud SQL client access
resource "google_service_account" "langfuse" {
  account_id   = "langfuse"
  display_name = "Langfuse"
  description  = "Langfuse LLM observability service — Cloud Run backed by Cloud SQL"
}

resource "google_project_iam_member" "langfuse_cloudsql" {
  project = var.project_id
  role    = "roles/cloudsql.client"
  member  = "serviceAccount:${google_service_account.langfuse.email}"
}

resource "google_project_iam_member" "langfuse_secret_accessor" {
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.langfuse.email}"
}

# MCP Server IAM: only needs Vertex AI access
resource "google_project_iam_member" "mcp_vertex_ai" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.mcp_server.email}"
}

# Job API IAM: Firestore + Pub/Sub publisher + Secret Manager
resource "google_project_iam_member" "job_api_firestore" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.job_api.email}"
}

resource "google_project_iam_member" "job_api_pubsub" {
  project = var.project_id
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:${google_service_account.job_api.email}"
}

resource "google_project_iam_member" "job_api_secret_accessor" {
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.job_api.email}"
}

# Agent Runtime IAM: Pub/Sub subscriber + Firestore + Vertex AI
resource "google_project_iam_member" "agent_pubsub" {
  project = var.project_id
  role    = "roles/pubsub.subscriber"
  member  = "serviceAccount:${google_service_account.agent_runtime.email}"
}

resource "google_project_iam_member" "agent_firestore" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.agent_runtime.email}"
}

resource "google_project_iam_member" "agent_vertex_ai" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.agent_runtime.email}"
}

resource "google_project_iam_member" "agent_secret_accessor" {
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.agent_runtime.email}"
}

# ---------------------------------------------------------------------------
# Workload Identity binding (GKE → GCP Service Account)
# Teaching note:
#   This is the KEY piece that allows GKE pods to call GCP APIs without
#   any API key or mounted credentials.
#
#   How it works:
#     1. GKE pod runs as Kubernetes SA: stock-agent/agent-runtime
#     2. This binding allows that K8s SA to impersonate the GCP SA
#     3. GCP SA has roles/aiplatform.user + pubsub.subscriber + etc.
#     4. Pod calls Vertex AI / Pub/Sub with no credentials in the container!
#
#   The annotation on the K8s ServiceAccount (in deployment.yaml) must match:
#     iam.gke.io/gcp-service-account: agent-runtime@PROJECT.iam.gserviceaccount.com
# ---------------------------------------------------------------------------

resource "google_service_account_iam_member" "workload_identity_agent" {
  service_account_id = google_service_account.agent_runtime.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[stock-agent/agent-runtime]"

  depends_on = [google_container_cluster.agent_cluster]
}

# GKE Autopilot nodes use the default compute SA to pull images from Artifact Registry.
# Without this, pods get ErrImagePull even though the image exists in AR.
resource "google_project_iam_member" "gke_node_ar_reader" {
  project = var.project_id
  role    = "roles/artifactregistry.reader"
  member  = "serviceAccount:${data.google_project.project.number}-compute@developer.gserviceaccount.com"
  depends_on = [google_project_service.apis]
}

# Also allow Pub/Sub to deliver dead-letter messages (needed for DLQ policy)
resource "google_project_iam_member" "pubsub_sa_token_creator" {
  project = var.project_id
  role    = "roles/iam.serviceAccountTokenCreator"
  member  = "serviceAccount:service-${data.google_project.project.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
  depends_on = [google_project_service.apis]
}

data "google_project" "project" {
  project_id = var.project_id
}

# ---------------------------------------------------------------------------
# Secret Manager
# Teaching note: Secrets are never in code. Cloud Run/GKE injects them at runtime.
# ---------------------------------------------------------------------------

resource "google_secret_manager_secret" "gemini_api_key" {
  secret_id = "gemini-api-key"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret" "internal_api_token" {
  secret_id = "internal-api-token"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret" "langfuse_secret_key" {
  secret_id = "langfuse-secret-key"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

# Langfuse-specific secrets
# Teaching note:
#   Langfuse needs several consistent secrets:
#   - langfuse-public-key   : pk-lf-xxx  (project API public key, safe to share)
#   - langfuse-db-url       : PostgreSQL connection URL (set by setup-gcp.sh)
#   - langfuse-nextauth-secret : NextAuth signing key (must be stable — changing it logs everyone out)
#   - langfuse-encryption-key  : Used to encrypt sensitive data IN the DB (changing it corrupts data!)

resource "google_secret_manager_secret" "langfuse_public_key" {
  secret_id = "langfuse-public-key"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret" "langfuse_db_url" {
  secret_id = "langfuse-db-url"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret" "langfuse_nextauth_secret" {
  secret_id = "langfuse-nextauth-secret"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret" "langfuse_encryption_key" {
  secret_id = "langfuse-encryption-key"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

# Langfuse admin password — fully managed by Terraform so teardown+setup is clean
# Teaching note: Using random_password + secret_version means the password is
# generated ONCE by Terraform and stored in Secret Manager automatically.
# terraform destroy deletes it; terraform apply creates a new one. No manual steps.
resource "random_password" "langfuse_admin" {
  length  = 16
  special = false
}

resource "google_secret_manager_secret" "langfuse_admin_password" {
  secret_id = "langfuse-admin-password"
  replication {
    auto {}
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "langfuse_admin_password" {
  secret      = google_secret_manager_secret.langfuse_admin_password.id
  secret_data = random_password.langfuse_admin.result
}

# ---------------------------------------------------------------------------
# Outputs (used by deploy scripts)
# ---------------------------------------------------------------------------

output "gke_cluster_name" {
  value = google_container_cluster.agent_cluster.name
}

output "redis_host" {
  value = google_redis_instance.cache.host
}

output "redis_port" {
  value = google_redis_instance.cache.port
}

output "artifact_registry" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/stock-agent"
}

output "pubsub_topic_requests" {
  value = google_pubsub_topic.analysis_requests.name
}

output "firestore_database" {
  value = google_firestore_database.default.name
}

output "langfuse_db_connection_name" {
  description = "Cloud SQL connection name — used to construct DATABASE_URL for Langfuse Cloud Run"
  value       = google_sql_database_instance.langfuse.connection_name
}

output "langfuse_db_password" {
  description = "Auto-generated Langfuse DB password — read by setup-gcp.sh to build DATABASE_URL"
  value       = random_password.langfuse_db.result
  sensitive   = true
}

output "langfuse_admin_password" {
  description = "Langfuse UI admin password — printed at end of setup-gcp.sh"
  value       = random_password.langfuse_admin.result
  sensitive   = true
}
