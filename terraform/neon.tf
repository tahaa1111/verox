# =============================================================================
# Neon PostgreSQL — medibox database
# =============================================================================

resource "neon_project" "medibox" {
  name      = var.neon_project_name
  region_id = var.neon_region
}

# Default branch (main) — created automatically by Neon
# We reference it for the connection string outputs

resource "neon_database" "medibox" {
  project_id = neon_project.medibox.id
  branch_id  = neon_project.medibox.default_branch_id
  name       = "neondb"
  owner_name = "neondb_owner"
}

# Least-privilege application role — CRUD only, no DDL
# Alembic migrations run as neondb_owner (superuser) in CI only
resource "neon_role" "app" {
  project_id = neon_project.medibox.id
  branch_id  = neon_project.medibox.default_branch_id
  name       = "medibox_app"
}
