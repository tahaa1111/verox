# =============================================================================
# Terraform outputs — connection strings and resource identifiers
# Sensitive outputs are marked sensitive=true (not shown in plan output)
# =============================================================================

# ── Neon ──────────────────────────────────────────────────────────────────────
output "neon_project_id" {
  description = "Neon project ID"
  value       = neon_project.medibox.id
}

output "neon_connection_string_pooled" {
  description = "Neon connection string (pooled — for app)"
  value       = neon_project.medibox.connection_string
  sensitive   = true
}

output "neon_branch_id" {
  description = "Neon default branch ID"
  value       = neon_project.medibox.default_branch_id
}

# ── Upstash ───────────────────────────────────────────────────────────────────
output "redis_url" {
  description = "Upstash Redis URL (rediss:// with password)"
  value = "rediss://default:${upstash_redis_database.medibox.password}@${upstash_redis_database.medibox.endpoint}:${upstash_redis_database.medibox.port}"
  sensitive   = true
}

output "upstash_redis_endpoint" {
  description = "Upstash Redis host:port"
  value       = "${upstash_redis_database.medibox.endpoint}:${upstash_redis_database.medibox.port}"
}

# ── Cloudflare R2 ─────────────────────────────────────────────────────────────
output "r2_bucket_name" {
  description = "R2 crops bucket name"
  value       = cloudflare_r2_bucket.crops.name
}

output "r2_endpoint" {
  description = "R2 S3-compatible endpoint URL"
  value       = "https://${var.cloudflare_account_id}.eu.r2.cloudflarestorage.com"
}

output "cloudflare_account_id" {
  description = "Cloudflare account ID"
  value       = var.cloudflare_account_id
}

# ── RunPod ────────────────────────────────────────────────────────────────────
output "vllm_url" {
  description = "RunPod OpenAI-compatible inference URL — set as VLLM_URL in Railway"
  value       = "https://api.runpod.ai/v2/${runpod_endpoint.qwen.id}/openai"
}

output "runpod_endpoint_id" {
  description = "RunPod serverless endpoint ID"
  value       = runpod_endpoint.qwen.id
}

# ── Railway env var block (copy-paste into Railway dashboard) ─────────────────
output "railway_api_env_vars" {
  description = "Paste this block into Railway medibox-api Variables → Raw Editor"
  sensitive   = true
  value = <<-EOT
ENVIRONMENT=production
DATABASE_URL=${neon_project.medibox.connection_string}
REDIS_URL=rediss://default:${upstash_redis_database.medibox.password}@${upstash_redis_database.medibox.endpoint}:${upstash_redis_database.medibox.port}
PII_ENCRYPTION_KEY=${var.pii_encryption_key}
R2_ENDPOINT=https://${var.cloudflare_account_id}.eu.r2.cloudflarestorage.com
R2_ACCESS_KEY_ID=${cloudflare_api_token.r2_medibox.value}
R2_BUCKET=medibox-crops
FIREBASE_PROJECT_ID=verox-4dc3f
METRICS_SECRET=${var.metrics_secret}
CAMERA_SECRET=${var.camera_secret}
VLLM_URL=https://api.runpod.ai/v2/${runpod_endpoint.qwen.id}/openai
VLLM_API_KEY=${var.runpod_api_key}
VLLM_MODEL=qwen/qwen2.5-vl-7b-instruct-awq
SENTRY_DSN=${var.sentry_dsn}
  EOT
}
