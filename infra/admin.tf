# Password for the /admin dashboard. The value is added with gcloud so it never ends up in state.

resource "google_secret_manager_secret" "admin_password" {
  secret_id = "admin-password"

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_iam_member" "web_admin_password" {
  secret_id = google_secret_manager_secret.admin_password.id
  role      = "roles/secretmanager.secretAccessor"
  member    = google_service_account.web.member
}
