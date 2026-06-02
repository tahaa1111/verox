# =============================================================================
# GitHub Actions secrets — all runtime credentials injected via CI
# Terraform manages these so they stay in sync with infrastructure changes.
# =============================================================================

locals {
  repo = "${var.github_owner}/${var.github_repo}"
}

# ── Database ──────────────────────────────────────────────────────────────────
resource "github_actions_secret" "neon_database_url" {
  repository      = var.github_repo
  secret_name     = "NEON_DATABASE_URL"
  plaintext_value = neon_project.medibox.connection_string
}

# ── Redis ─────────────────────────────────────────────────────────────────────
resource "github_actions_secret" "redis_url" {
  repository      = var.github_repo
  secret_name     = "REDIS_URL"
  plaintext_value = "rediss://default:${upstash_redis_database.medibox.password}@${upstash_redis_database.medibox.endpoint}:${upstash_redis_database.medibox.port}"
}

# ── Cloudflare R2 ─────────────────────────────────────────────────────────────
resource "github_actions_secret" "r2_access_key_id" {
  repository      = var.github_repo
  secret_name     = "R2_ACCESS_KEY_ID"
  plaintext_value = cloudflare_api_token.r2_medibox.value
}

resource "github_actions_secret" "cloudflare_account_id" {
  repository      = var.github_repo
  secret_name     = "CF_ACCOUNT_ID"
  plaintext_value = var.cloudflare_account_id
}

# ── PII encryption ────────────────────────────────────────────────────────────
resource "github_actions_secret" "pii_encryption_key" {
  repository      = var.github_repo
  secret_name     = "PII_ENCRYPTION_KEY"
  plaintext_value = var.pii_encryption_key
}

resource "github_actions_secret" "pii_encryption_key_prev" {
  repository      = var.github_repo
  secret_name     = "PII_ENCRYPTION_KEY_PREV"
  plaintext_value = var.pii_encryption_key_prev
}

# ── Application ───────────────────────────────────────────────────────────────
resource "github_actions_secret" "firebase_admin_json" {
  repository      = var.github_repo
  secret_name     = "FIREBASE_ADMIN_JSON"
  plaintext_value = var.firebase_admin_json
}

resource "github_actions_secret" "metrics_secret" {
  repository      = var.github_repo
  secret_name     = "METRICS_SECRET"
  plaintext_value = var.metrics_secret
}

resource "github_actions_secret" "camera_secret" {
  repository      = var.github_repo
  secret_name     = "CAMERA_SECRET"
  plaintext_value = var.camera_secret
}

resource "github_actions_secret" "sentry_dsn" {
  repository      = var.github_repo
  secret_name     = "SENTRY_DSN"
  plaintext_value = var.sentry_dsn
}

# ── Railway deploy token ──────────────────────────────────────────────────────
# Set manually: add RAILWAY_TOKEN to GitHub secrets via Railway dashboard
# resource "github_actions_secret" "railway_token" { ... }
