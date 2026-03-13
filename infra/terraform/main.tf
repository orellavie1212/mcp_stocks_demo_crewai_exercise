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
    "run.googleapis.com",           # Cloud Run
    "container.googleapis.com",     # GKE
    "pubsub.googleapis.com",        # Pub/Sub
    "firestore.googleapis.com",     # Firestore
    "redis.googleapis.com",         # Memorystore Redis
    "sqladmin.googleapis.com",      # Cloud SQL (for Langfuse)
    "secretmanager.googleapis.com", # Secret Manager
    "artifactregistry.googleapis.com", # Artifact Registry
    "cloudbuild.googleapis.com",    # Cloud Build
    "monitoring.googleapis.com",    # Cloud Monitoring
    "logging.googleapis.com",       # Cloud Logging
    "cloudtrace.googleapis.com",    # Cloud Trace
    "iam.googleapis.com",           # IAM
    "aiplatform.googleapis.com",    # Vertex AI (Gemini)
  ])
  service                    = each.value
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

  # Dead letter after 3 failed attempts
  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.analysis_dlq.id
    max_delivery_attempts = 3
  }

  depends_on = [google_project_service.apis]
}

# ---------------------------------------------------------------------------
# Firestore (job state storage)
# Teaching note: Serverless, no instance to manage, real-time listeners built-in
# ---------------------------------------------------------------------------

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

resource "google_sql_database_instance" "langfuse" {
  name             = "langfuse-db"
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
      # Private IP only — more secure, no public exposure
      ipv4_enabled    = false
      private_network = google_compute_network.vpc.id
    }
  }
  depends_on = [google_project_service.apis]
}

resource "google_sql_database" "langfuse" {
  name     = "langfuse"
  instance = google_sql_database_instance.langfuse.name
}

resource "google_sql_user" "langfuse" {
  name     = "langfuse"
  instance = google_sql_database_instance.langfuse.name
  password = random_password.langfuse_db.result
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
  enable_autopilot = true

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

# MCP Server IAM: only needs Vertex AI access
resource "google_project_iam_member" "mcp_vertex_ai" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.mcp_server.email}"
}

# Job API IAM: Firestore + Pub/Sub publisher
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
