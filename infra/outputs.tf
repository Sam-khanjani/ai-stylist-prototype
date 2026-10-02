output "web_url" {
  value = google_cloud_run_v2_service.web.uri
}

output "api_url" {
  value = google_cloud_run_v2_service.api.uri
}

output "registry" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.app.repository_id}"
}

output "wif_provider" {
  value = google_iam_workload_identity_pool_provider.github.name
}

output "deployer_email" {
  value = google_service_account.deployer.email
}

output "catalog_bucket" {
  value = google_storage_bucket.catalog_images.name
}

output "photos_bucket" {
  value = google_storage_bucket.user_photos.name
}
