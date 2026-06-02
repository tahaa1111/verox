# =============================================================================
# Medibox — Terraform variables
# All secrets are marked sensitive=true and sourced from environment variables.
# Never commit real values. Use: export TF_VAR_<name>=<value>
# Or create terraform.tfvars (add to .gitignore).
# =============================================================================

# ── Neon ──────────────────────────────────────────────────────────────────────
variable "neon_api_key" {
  description = "Neon API key — https://console.neon.tech/app/settings/api-keys"
  type        = string
  sensitive   = true
}

variable "neon_project_name" {
  description = "Neon project name"
  type        = string
  default     = "medibox"
}

variable "neon_region" {
  description = "Neon region"
  type        = string
  default     = "aws-eu-central-1"
}

# ── Upstash ───────────────────────────────────────────────────────────────────
variable "upstash_email" {
  description = "Upstash account email"
  type        = string
}

variable "upstash_api_key" {
  description = "Upstash API key — https://console.upstash.com/account/api"
  type        = string
  sensitive   = true
}

variable "upstash_region" {
  description = "Upstash Redis region"
  type        = string
  default     = "eu-central-1"
}

# ── Cloudflare ────────────────────────────────────────────────────────────────
variable "cloudflare_api_token" {
  description = "Cloudflare API token with R2 read/write permissions"
  type        = string
  sensitive   = true
}

variable "cloudflare_account_id" {
  description = "Cloudflare account ID (shown on R2 overview page)"
  type        = string
  default     = "8590a06fc227f954c605d19a4456c53f"
}

# ── GitHub ────────────────────────────────────────────────────────────────────
variable "github_token" {
  description = "GitHub personal access token with repo + secrets permissions"
  type        = string
  sensitive   = true
}

variable "github_owner" {
  description = "GitHub username or organisation"
  type        = string
  default     = "tahaa1111"
}

variable "github_repo" {
  description = "GitHub repository name"
  type        = string
  default     = "verox"
}

# ── RunPod (fill in when credentials available) ───────────────────────────────
variable "runpod_api_key" {
  description = "RunPod API key — https://www.runpod.io/console/user/settings"
  type        = string
  sensitive   = true
  default     = ""  # set when RunPod endpoint is created
}

# ── PII encryption keys (passed through to GitHub secrets) ────────────────────
variable "pii_encryption_key" {
  description = "MultiFernet current key — generate: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
  type        = string
  sensitive   = true
  default     = "4Od5bcywunm0AaH_uy__U-Is-Q20Mv52Ql5Sp1Ue1ME="
}

variable "pii_encryption_key_prev" {
  description = "MultiFernet previous key (rotation window)"
  type        = string
  sensitive   = true
  default     = ""
}

# ── Application secrets ───────────────────────────────────────────────────────
variable "firebase_admin_json" {
  description = "Firebase Admin SDK service account JSON (full file contents)"
  type        = string
  sensitive   = true
  default     = ""
}

variable "metrics_secret" {
  description = "Bearer token for Prometheus /metrics endpoint"
  type        = string
  sensitive   = true
  default     = "mI7mj9w_7dXfqhptuFAMx1EhXoV2CB-acamGu6-O0pA"
}

variable "camera_secret" {
  description = "HMAC secret for Pi camera relay authentication"
  type        = string
  sensitive   = true
  default     = "medibox-camera-prod-secret-2026"
}

variable "sentry_dsn" {
  description = "Sentry project DSN for error tracking"
  type        = string
  sensitive   = true
  default     = ""
}
