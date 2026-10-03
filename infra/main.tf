terraform {
  required_version = ">= 1.6"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.0"
    }
  }

  # Bucket is created once by hand before init
  backend "gcs" {
    bucket = "ai-stylist-proto-tfstate"
    prefix = "infra"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  apis = [
    "cloudresourcemanager.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "sts.googleapis.com",
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "secretmanager.googleapis.com",
    "storage.googleapis.com",
    "aiplatform.googleapis.com",
    "sqladmin.googleapis.com",
    "cloudscheduler.googleapis.com",
  ]

  # Cloud Run needs an image on first create; real images are deployed later
  placeholder_image = "us-docker.pkg.dev/cloudrun/container/hello"
}

resource "google_project_service" "apis" {
  for_each           = toset(local.apis)
  service            = each.value
  disable_on_destroy = false
}

# --- Artifact Registry ---

resource "google_artifact_registry_repository" "app" {
  location      = var.region
  repository_id = "app"
  format        = "DOCKER"

  depends_on = [google_project_service.apis]
}

# --- Service accounts ---

resource "google_service_account" "api" {
  account_id   = "api-runner"
  display_name = "Cloud Run api"
  depends_on   = [google_project_service.apis]
}

resource "google_service_account" "web" {
  account_id   = "web-runner"
  display_name = "Cloud Run web"
  depends_on   = [google_project_service.apis]
}

resource "google_project_iam_member" "api_vertex" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = google_service_account.api.member
}

# --- Secrets ---
# Only the container is managed here. The value is added with gcloud so it never ends up in state.

resource "google_secret_manager_secret" "llm_api_key" {
  secret_id = "llm-api-key"

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_iam_member" "api_llm_key" {
  secret_id = google_secret_manager_secret.llm_api_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = google_service_account.api.member
}

resource "google_secret_manager_secret" "langfuse" {
  for_each  = toset(["langfuse-public-key", "langfuse-secret-key"])
  secret_id = each.value

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_iam_member" "api_langfuse" {
  for_each  = google_secret_manager_secret.langfuse
  secret_id = each.value.id
  role      = "roles/secretmanager.secretAccessor"
  member    = google_service_account.api.member
}

# --- Buckets ---

resource "google_storage_bucket" "catalog_images" {
  name                        = "${var.project_id}-catalog-images"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  depends_on = [google_project_service.apis]
}

resource "google_storage_bucket_iam_member" "api_buckets" {
  # Try-on photos are never stored, so the catalog is the only bucket the api uses
  for_each = {
    catalog = google_storage_bucket.catalog_images.name
  }
  bucket = each.value
  role   = "roles/storage.objectAdmin"
  member = google_service_account.api.member
}

# --- Cloud Run ---

resource "google_cloud_run_v2_service" "api" {
  name                = "api"
  location            = var.region
  deletion_protection = false

  template {
    service_account = google_service_account.api.email
    # needed for the bucket volume
    execution_environment = "EXECUTION_ENVIRONMENT_GEN2"

    scaling {
      max_instance_count = 2
    }

    volumes {
      name = "data"
      gcs {
        bucket    = google_storage_bucket.catalog_images.name
        read_only = true
      }
    }

    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [google_sql_database_instance.main.connection_name]
      }
    }

    containers {
      image = local.placeholder_image

      volume_mounts {
        name       = "data"
        mount_path = "/data"
      }

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }

      env {
        name  = "PGHOST"
        value = "/cloudsql/${google_sql_database_instance.main.connection_name}"
      }

      env {
        name  = "PGUSER"
        value = google_sql_user.app.name
      }

      env {
        name  = "PGDATABASE"
        value = google_sql_database.stylist.name
      }

      env {
        name = "PGPASSWORD"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.db_password.secret_id
            version = "latest"
          }
        }
      }

      env {
        name  = "CATALOG_PATH"
        value = "/data/raw/products.jsonl"
      }

      # Chat history retention in days; keep in sync with the text in the chat widget
      env {
        name  = "CHAT_RETENTION_DAYS"
        value = "30"
      }

      env {
        name  = "LANGFUSE_BASE_URL"
        value = "https://cloud.langfuse.com"
      }

      env {
        name = "LANGFUSE_PUBLIC_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.langfuse["langfuse-public-key"].secret_id
            version = "latest"
          }
        }
      }

      env {
        name = "LANGFUSE_SECRET_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.langfuse["langfuse-secret-key"].secret_id
            version = "latest"
          }
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].containers[0].image, client, client_version]
  }

  depends_on = [
    google_project_service.apis,
    google_secret_manager_secret_iam_member.api_langfuse,
    google_secret_manager_secret_iam_member.api_db_password,
    google_secret_manager_secret_version.db_password,
    google_project_iam_member.api_sql,
  ]
}

resource "google_cloud_run_v2_service" "web" {
  name                = "web"
  location            = var.region
  deletion_protection = false

  template {
    service_account = google_service_account.web.email

    scaling {
      max_instance_count = 2
    }

    containers {
      image = local.placeholder_image

      env {
        name  = "API_URL"
        value = google_cloud_run_v2_service.api.uri
      }

      env {
        name = "ADMIN_PASSWORD"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.admin_password.secret_id
            version = "latest"
          }
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].containers[0].image, client, client_version]
  }

  depends_on = [google_project_service.apis, google_secret_manager_secret_iam_member.web_admin_password]
}

# web is public, api only accepts calls from web
resource "google_cloud_run_v2_service_iam_member" "web_public" {
  name     = google_cloud_run_v2_service.web.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_service_iam_member" "api_from_web" {
  name     = google_cloud_run_v2_service.api.name
  location = var.region
  role     = "roles/run.invoker"
  member   = google_service_account.web.member
}
