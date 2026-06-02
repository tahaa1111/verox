# =============================================================================
# Upstash Redis — broker + rate-limit + pub/sub + idempotency cache
# =============================================================================

resource "upstash_redis_database" "medibox" {
  database_name = "medibox"
  region        = var.upstash_region
  tls           = true   # enforces rediss:// — plain redis:// connections rejected
}
