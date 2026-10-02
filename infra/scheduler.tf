# Daily job that deletes chat history older than 30 days (GDPR retention)

resource "google_service_account" "scheduler" {
  account_id   = "scheduler"
  display_name = "Cloud Scheduler jobs"
  depends_on   = [google_project_service.apis]
}

resource "google_cloud_run_v2_service_iam_member" "api_from_scheduler" {
  name     = google_cloud_run_v2_service.api.name
  location = var.region
  role     = "roles/run.invoker"
  member   = google_service_account.scheduler.member
}

resource "google_cloud_scheduler_job" "chat_cleanup" {
  name      = "chat-history-cleanup"
  region    = "europe-west1" # Cloud Scheduler isn't offered in every region; this keeps it in the EU
  schedule  = "0 3 * * *"
  time_zone = "Europe/Amsterdam"

  http_target {
    http_method = "POST"
    uri         = "${google_cloud_run_v2_service.api.uri}/maintenance/cleanup"

    oidc_token {
      service_account_email = google_service_account.scheduler.email
      audience              = google_cloud_run_v2_service.api.uri
    }
  }

  depends_on = [google_project_service.apis]
}
