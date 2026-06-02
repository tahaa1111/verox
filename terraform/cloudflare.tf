# =============================================================================
# Cloudflare R2 — private crop storage
# All objects are private. Served exclusively via presigned URLs (TTL 300s).
# =============================================================================

resource "cloudflare_r2_bucket" "crops" {
  account_id = var.cloudflare_account_id
  name       = "medibox-crops"
  location   = "EEUR"  # Eastern Europe — closest to Tunisia
}

# Terraform state bucket (separate from crops)
resource "cloudflare_r2_bucket" "terraform_state" {
  account_id = var.cloudflare_account_id
  name       = "medibox-terraform-state"
  location   = "EEUR"
}

# Dedicated R2 API token — Object Read & Write on medibox-crops only
resource "cloudflare_api_token" "r2_medibox" {
  name = "medibox-r2-rw"

  policy {
    permission_groups = [
      # Object Read & Write
      data.cloudflare_api_token_permission_groups.all.object_read_write["Workers R2 Storage Bucket Item Read"],
      data.cloudflare_api_token_permission_groups.all.object_read_write["Workers R2 Storage Bucket Item Write"],
    ]
    resources = {
      "com.cloudflare.edge.r2.bucket.${var.cloudflare_account_id}_default_${cloudflare_r2_bucket.crops.name}" = "*"
    }
  }
}

data "cloudflare_api_token_permission_groups" "all" {}
