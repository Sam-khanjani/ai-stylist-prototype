# Postgres with pgvector for the knowledge base

resource "google_sql_database_instance" "main" {
  name                = "stylist-db"
  database_version    = "POSTGRES_17"
  region              = var.region
  deletion_protection = true

  settings {
    edition           = "ENTERPRISE"
    tier              = "db-f1-micro" # smallest shared-core tier, enough for a prototype
    disk_size         = 10
    disk_autoresize   = true
    availability_type = "ZONAL"

    # Public IP but no authorized networks: only reachable through the Cloud SQL proxy/connector with IAM
    ip_configuration {
      ipv4_enabled = true
      ssl_mode     = "ENCRYPTED_ONLY"
    }
  }

  depends_on = [google_project_service.apis]
}

resource "google_sql_database" "stylist" {
  name     = "stylist"
  instance = google_sql_database_instance.main.name
}

resource "random_password" "db" {
  length  = 32
  special = false
}

resource "google_sql_user" "app" {
  name     = "app"
  instance = google_sql_database_instance.main.name
  password = random_password.db.result
}

resource "google_secret_manager_secret" "db_password" {
  secret_id = "db-password"

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "db_password" {
  secret      = google_secret_manager_secret.db_password.id
  secret_data = random_password.db.result
}
