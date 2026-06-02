#!/usr/bin/env bash
# =============================================================================
# Run Alembic migrations via Cloud SQL Auth Proxy
# =============================================================================
set -euo pipefail

: "${PROJECT_ID:?PROJECT_ID must be set}"
: "${DB_PASSWORD:?DB_PASSWORD must be set (from Secret Manager)}"

INSTANCE="${INSTANCE:-${PROJECT_ID}:${REGION:-us-central1}:medibox-postgres}"
DB_USER="${DB_USER:-medibox}"
DB_NAME="${DB_NAME:-medibox}"
PROXY_PORT=5433

log() { echo "[$(date -u +%H:%M:%S)] $*"; }

# Download Auth Proxy if not present
if ! command -v cloud-sql-proxy &>/dev/null; then
  log "Downloading Cloud SQL Auth Proxy"
  curl -sSL https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.13.0/cloud-sql-proxy.linux.amd64 \
    -o /tmp/cloud-sql-proxy
  chmod +x /tmp/cloud-sql-proxy
  PROXY=/tmp/cloud-sql-proxy
else
  PROXY=cloud-sql-proxy
fi

log "Starting Auth Proxy for $INSTANCE on port $PROXY_PORT"
"$PROXY" "$INSTANCE" --port "$PROXY_PORT" --quiet &
PROXY_PID=$!
sleep 3

export DATABASE_URL_SYNC="postgresql+psycopg2://${DB_USER}:${DB_PASSWORD}@127.0.0.1:${PROXY_PORT}/${DB_NAME}"
export DB_USER DB_PASSWORD DB_NAME
export DB_HOST=127.0.0.1
export DB_PORT=$PROXY_PORT

log "Running Alembic migrations"
cd "$(dirname "$0")/.."
python -m alembic -c migrations/alembic.ini upgrade head

log "Migrations complete"
kill "$PROXY_PID" 2>/dev/null || true
