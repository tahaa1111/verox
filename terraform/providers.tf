terraform {
  required_version = ">= 1.7.0"

  required_providers {
    # Neon PostgreSQL — https://registry.terraform.io/providers/neon-database/neon
    neon = {
      source  = "neon-database/neon"
      version = "~> 0.6"
    }

    # Upstash Redis — https://registry.terraform.io/providers/upstash/upstash
    upstash = {
      source  = "upstash/upstash"
      version = "~> 1.5"
    }

    # Cloudflare (R2 bucket + API tokens) — https://registry.terraform.io/providers/cloudflare/cloudflare
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 4.40"
    }

    # GitHub (repository secrets) — https://registry.terraform.io/providers/integrations/github
    github = {
      source  = "integrations/github"
      version = "~> 6.3"
    }

    # RunPod Serverless — https://registry.terraform.io/providers/runpod-io/runpod
    runpod = {
      source  = "runpod-io/runpod"
      version = "~> 1.3"
    }
  }

  # Remote state — store in R2 (S3-compatible) so it's never local
  # Uncomment after first apply (chicken-and-egg: R2 bucket must exist first)
  # backend "s3" {
  #   bucket                      = "medibox-terraform-state"
  #   key                         = "medibox/terraform.tfstate"
  #   region                      = "auto"
  #   endpoint                    = "https://${var.cloudflare_account_id}.r2.cloudflarestorage.com"
  #   access_key                  = var.r2_access_key_id
  #   secret_key                  = var.r2_secret_key
  #   skip_credentials_validation = true
  #   skip_metadata_api_check     = true
  #   skip_region_validation      = true
  #   force_path_style            = true
  # }
}

# ── Provider configurations ────────────────────────────────────────────────

provider "neon" {
  api_key = var.neon_api_key
}

provider "upstash" {
  email   = var.upstash_email
  api_key = var.upstash_api_key
}

provider "cloudflare" {
  api_token = var.cloudflare_api_token
}

provider "github" {
  token = var.github_token
  owner = var.github_owner
}

provider "runpod" {
  api_key = var.runpod_api_key
}
