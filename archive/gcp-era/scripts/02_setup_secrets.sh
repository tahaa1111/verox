#!/usr/bin/env bash
# =============================================================================
# Medibox — Secret Manager Setup
# Generates and uploads all production secrets.
# Run AFTER 01_setup_project.sh.
#
# Usage (interactive):
#   set -a && source .env.deploy && set +a
#   bash scripts/02_setup_secrets.sh
#
# Usage (non-interactive, all values from env):
#   export FIREBASE_ADMIN_JSON_PATH=/path/to/serviceAccountKey.json
#   export NONINTERACTIVE=true
#   set -a && source .env.deploy && set +a
#   bash scripts/02_setup_secrets.sh
# =============================================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

ok()     { echo -e "  ${GREEN}✔${NC}  $*"; }
fail()   { echo -e "  ${RED}✘${NC}  $*" >&2; }
warn()   { echo -e "  ${YELLOW}⚠${NC}  $*"; }
info()   { echo -e "  ${BLUE}ℹ${NC}  $*"; }
header() { echo -e "\n${BOLD}${BLUE}── $* ──${NC}"; }

# Auto-source .env.deploy if PROJECT_ID is not already set
if [[ -z "${PROJECT_ID:-}" && -f ".env.deploy" ]]; then
  set -a && source ".env.deploy" && set +a
elif [[ -z "${PROJECT_ID:-}" && -f "../.env.deploy" ]]; then
  set -a && source "../.env.deploy" && set +a
fi

: "${PROJECT_ID:?PROJECT_ID must be set — run: set -a && source .env.deploy && set +a}"
REGION="${REGION:-us-central1}"
NONINTERACTIVE="${NONINTERACTIVE:-false}"
RESOURCES_FILE="gcp_resources.txt"
MANIFEST_FILE="secrets_manifest.txt"

RUNNER_EMAIL="medibox-runner@${PROJECT_ID}.iam.gserviceaccount.com"

# Check prerequisites
if [[ ! -f "$RESOURCES_FILE" ]]; then
  fail "gcp_resources.txt not found — run 01_setup_project.sh first"
  exit 1
fi

echo -e "${BOLD}Medibox — Secret Manager Setup${NC}"
echo "Project: $PROJECT_ID"
echo "========================================"

# Helper: create or update a secret
upsert_secret() {
  local name="$1"
  local data_file="$2"
  local description="${3:-}"

  if gcloud secrets describe "$name" --project="$PROJECT_ID" &>/dev/null; then
    # Add new version
    gcloud secrets versions add "$name" \
      --data-file="$data_file" \
      --project="$PROJECT_ID"
    ok "Updated: $name (new version added)"
  else
    # Create new secret
    gcloud secrets create "$name" \
      --data-file="$data_file" \
      --labels="app=medibox" \
      --project="$PROJECT_ID"
    ok "Created: $name"
  fi

  # Ensure runner SA has access
  gcloud secrets add-iam-policy-binding "$name" \
    --member="serviceAccount:${RUNNER_EMAIL}" \
    --role="roles/secretmanager.secretAccessor" \
    --project="$PROJECT_ID" \
    --quiet >/dev/null

  # Record in manifest
  echo "$name" >> "$MANIFEST_FILE"
}

# Helper: verify a secret is retrievable
verify_secret() {
  local name="$1"
  if gcloud secrets versions access latest \
       --secret="$name" --project="$PROJECT_ID" &>/dev/null; then
    ok "Verified retrieval: $name"
    return 0
  else
    fail "CANNOT retrieve: $name"
    return 1
  fi
}

prompt_or_env() {
  # Usage: prompt_or_env VAR_NAME "Prompt text"
  local var_name="$1"
  local prompt_text="$2"
  if [[ -n "${!var_name:-}" ]]; then
    info "$var_name set from environment"
  elif [[ "$NONINTERACTIVE" == "true" ]]; then
    fail "$var_name must be set in non-interactive mode"
    exit 1
  else
    echo -e "${YELLOW}  → ${prompt_text}:${NC} "
    read -r input
    export "$var_name=$input"
  fi
}

# Fresh manifest
echo "# Medibox secrets manifest — $(date -u '+%Y-%m-%d %H:%M:%S UTC')" > "$MANIFEST_FILE"
echo "# All secrets are in project: $PROJECT_ID" >> "$MANIFEST_FILE"
echo "" >> "$MANIFEST_FILE"

VERIFY_FAILURES=0

# ---------------------------------------------------------------------------
# 1. Postgres password
# ---------------------------------------------------------------------------
header "1 · Postgres Password"

if gcloud secrets versions access latest \
     --secret=medibox-db-password --project="$PROJECT_ID" &>/dev/null; then
  EXISTING_VAL=$(gcloud secrets versions access latest \
    --secret=medibox-db-password --project="$PROJECT_ID" 2>/dev/null)
  if [[ "$EXISTING_VAL" != "REPLACE_ME_WITH_REAL_VALUE" ]]; then
    ok "medibox-db-password already has a real value (set by 01_setup_project.sh)"
    echo "medibox-db-password (set by 01_setup_project.sh)" >> "$MANIFEST_FILE"
  else
    info "Generating new Postgres password"
    DB_PASS=$(python3 -c "import secrets; print(secrets.token_urlsafe(40))")
    echo -n "$DB_PASS" > /tmp/mb_db_pass.tmp
    upsert_secret "medibox-db-password" "/tmp/mb_db_pass.tmp" "Medibox Postgres user password"
    rm -f /tmp/mb_db_pass.tmp
    ok "Postgres password generated and stored"

    info "Setting password on Cloud SQL user"
    gcloud sql users set-password medibox \
      --instance=medibox-postgres \
      --password="$DB_PASS" \
      --project="$PROJECT_ID" 2>/dev/null || \
      warn "Could not set SQL password — do it manually: gcloud sql users set-password medibox --instance=medibox-postgres --password=<value from secret>"
  fi
else
  info "Generating Postgres password"
  DB_PASS=$(python3 -c "import secrets; print(secrets.token_urlsafe(40))")
  echo -n "$DB_PASS" > /tmp/mb_db_pass.tmp
  upsert_secret "medibox-db-password" "/tmp/mb_db_pass.tmp" "Medibox Postgres user password"
  rm -f /tmp/mb_db_pass.tmp
fi
verify_secret "medibox-db-password" || VERIFY_FAILURES=$((VERIFY_FAILURES + 1))

# ---------------------------------------------------------------------------
# 2. Redis AUTH string
# ---------------------------------------------------------------------------
header "2 · Redis AUTH String"

REDIS_AUTH_EXISTING=$(gcloud secrets versions access latest \
  --secret=medibox-redis-auth --project="$PROJECT_ID" 2>/dev/null || echo "")

if [[ -n "$REDIS_AUTH_EXISTING" && "$REDIS_AUTH_EXISTING" != "REPLACE_ME_WITH_REAL_VALUE" ]]; then
  ok "medibox-redis-auth already set (from 01_setup_project.sh)"
else
  info "Retrieving Redis AUTH string from Memorystore"
  REDIS_AUTH=$(gcloud redis instances get-auth-string medibox-redis \
    --region="$REGION" --project="$PROJECT_ID" \
    --format="value(authString)" 2>/dev/null || echo "")
  if [[ -z "$REDIS_AUTH" ]]; then
    warn "Could not retrieve Redis AUTH from Memorystore — generating random value"
    REDIS_AUTH=$(python3 -c "import secrets; print(secrets.token_urlsafe(40))")
  fi
  echo -n "$REDIS_AUTH" > /tmp/mb_redis.tmp
  upsert_secret "medibox-redis-auth" "/tmp/mb_redis.tmp" "Memorystore Redis AUTH string"
  rm -f /tmp/mb_redis.tmp
fi
verify_secret "medibox-redis-auth" || VERIFY_FAILURES=$((VERIFY_FAILURES + 1))

# ---------------------------------------------------------------------------
# 3. JWT Signing Key
# ---------------------------------------------------------------------------
header "3 · JWT Signing Key"

JWT_EXISTING=$(gcloud secrets versions access latest \
  --secret=medibox-jwt-signing-key --project="$PROJECT_ID" 2>/dev/null || echo "")

if [[ -n "$JWT_EXISTING" && "$JWT_EXISTING" != "REPLACE_ME_WITH_REAL_VALUE" ]]; then
  ok "medibox-jwt-signing-key already has a real value"
else
  info "Generating 64-byte JWT signing key (base64)"
  JWT_KEY=$(python3 -c "import secrets,base64; print(base64.b64encode(secrets.token_bytes(64)).decode())")
  echo -n "$JWT_KEY" > /tmp/mb_jwt.tmp
  upsert_secret "medibox-jwt-signing-key" "/tmp/mb_jwt.tmp" "JWT signing secret"
  rm -f /tmp/mb_jwt.tmp
fi
verify_secret "medibox-jwt-signing-key" || VERIFY_FAILURES=$((VERIFY_FAILURES + 1))

# ---------------------------------------------------------------------------
# 4. PII Encryption Key (Fernet — dev path, KMS is prod path)
# ---------------------------------------------------------------------------
header "4 · PII Encryption Key (dev fallback)"

PII_EXISTING=$(gcloud secrets versions access latest \
  --secret=medibox-pii-encryption-key --project="$PROJECT_ID" 2>/dev/null || echo "")

if [[ -n "$PII_EXISTING" && "$PII_EXISTING" != "REPLACE_ME_WITH_REAL_VALUE" ]]; then
  ok "medibox-pii-encryption-key already has a real value"
else
  info "Generating Fernet key for PII encryption dev fallback"
  PII_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" 2>/dev/null || \
            python3 -c "import secrets,base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())")
  echo -n "$PII_KEY" > /tmp/mb_pii.tmp
  upsert_secret "medibox-pii-encryption-key" "/tmp/mb_pii.tmp" "PII Fernet encryption key (dev path)"
  rm -f /tmp/mb_pii.tmp
fi
verify_secret "medibox-pii-encryption-key" || VERIFY_FAILURES=$((VERIFY_FAILURES + 1))

# ---------------------------------------------------------------------------
# 5. Firebase Admin SDK JSON
# ---------------------------------------------------------------------------
header "5 · Firebase Admin SDK JSON"

FB_EXISTING=$(gcloud secrets versions access latest \
  --secret=medibox-firebase-admin-json --project="$PROJECT_ID" 2>/dev/null || echo "")

if [[ -n "$FB_EXISTING" && "$FB_EXISTING" != "REPLACE_ME_WITH_REAL_VALUE" ]]; then
  ok "medibox-firebase-admin-json already has a real value"
else
  prompt_or_env FIREBASE_ADMIN_JSON_PATH \
    "Enter the full path to your Firebase Admin SDK JSON file (e.g. ~/Downloads/firebase-adminsdk.json)"

  FIREBASE_ADMIN_JSON_PATH="${FIREBASE_ADMIN_JSON_PATH/#\~/$HOME}"

  if [[ ! -f "$FIREBASE_ADMIN_JSON_PATH" ]]; then
    fail "File not found: $FIREBASE_ADMIN_JSON_PATH"
    echo ""
    echo "To get this file:"
    echo "  1. Go to Firebase Console → Project Settings → Service Accounts"
    echo "  2. Click 'Generate new private key'"
    echo "  3. Download the JSON file"
    echo "  4. Re-run this script with: export FIREBASE_ADMIN_JSON_PATH=/path/to/file.json"
    exit 1
  fi

  # Validate the JSON
  if ! python3 -c "
import json, sys
d = json.load(open('$FIREBASE_ADMIN_JSON_PATH'))
required = ['project_id', 'private_key', 'client_email', 'type']
missing = [k for k in required if k not in d]
if missing:
    print('Missing fields: ' + ', '.join(missing))
    sys.exit(1)
assert d['type'] == 'service_account', 'Expected type=service_account'
print(f'Valid Firebase admin JSON for project: {d[\"project_id\"]}')
" 2>&1; then
    fail "Firebase admin JSON is invalid"
    exit 1
  fi

  upsert_secret "medibox-firebase-admin-json" "$FIREBASE_ADMIN_JSON_PATH" "Firebase Admin SDK"
fi
verify_secret "medibox-firebase-admin-json" || VERIFY_FAILURES=$((VERIFY_FAILURES + 1))

# ---------------------------------------------------------------------------
# 6. Database URL (constructed — not a user secret, but stored for convenience)
# ---------------------------------------------------------------------------
header "6 · Database URL (constructed)"

# Build connection string from components
SQL_CONN="${PROJECT_ID}:${REGION}:medibox-postgres"
DB_PASS_VAL=$(gcloud secrets versions access latest \
  --secret=medibox-db-password --project="$PROJECT_ID" 2>/dev/null || echo "")

if [[ -n "$DB_PASS_VAL" ]]; then
  # Cloud SQL Auth Proxy socket path (used by Cloud Run)
  # Format: postgresql+asyncpg://user:pass@/dbname?host=/cloudsql/PROJECT:REGION:INSTANCE
  DB_URL="postgresql+asyncpg://medibox:${DB_PASS_VAL}@/medibox?host=/cloudsql/${SQL_CONN}"
  echo -n "$DB_URL" > /tmp/mb_dburl.tmp
  upsert_secret "medibox-database-url" "/tmp/mb_dburl.tmp" "Full async DB URL for Cloud Run"
  rm -f /tmp/mb_dburl.tmp

  # Sync URL for Alembic migrations
  DB_URL_SYNC="postgresql://medibox:${DB_PASS_VAL}@/medibox?host=/cloudsql/${SQL_CONN}"
  echo -n "$DB_URL_SYNC" > /tmp/mb_dburl_sync.tmp
  upsert_secret "medibox-database-url-sync" "/tmp/mb_dburl_sync.tmp" "Sync DB URL for Alembic"
  rm -f /tmp/mb_dburl_sync.tmp
  verify_secret "medibox-database-url" || VERIFY_FAILURES=$((VERIFY_FAILURES + 1))
  verify_secret "medibox-database-url-sync" || VERIFY_FAILURES=$((VERIFY_FAILURES + 1))
fi

# ---------------------------------------------------------------------------
# 7. Print manifest
# ---------------------------------------------------------------------------
header "Secrets Manifest"

echo ""
echo -e "${BOLD}Secrets created/verified in project ${PROJECT_ID}:${NC}"
echo ""
printf "  %-40s  %s\n" "Secret Name" "Status"
printf "  %-40s  %s\n" "──────────────────────────────────────" "──────"

for secret_name in \
  medibox-db-password \
  medibox-database-url \
  medibox-database-url-sync \
  medibox-redis-auth \
  medibox-jwt-signing-key \
  medibox-pii-encryption-key \
  medibox-firebase-admin-json; do

  VAL=$(gcloud secrets versions access latest \
    --secret="$secret_name" --project="$PROJECT_ID" 2>/dev/null || echo "")
  if [[ -n "$VAL" && "$VAL" != "REPLACE_ME_WITH_REAL_VALUE" ]]; then
    printf "  ${GREEN}✔${NC}  %-38s  %s\n" "$secret_name" "SET (${#VAL} chars)"
  else
    printf "  ${RED}✘${NC}  %-38s  %s\n" "$secret_name" "MISSING or PLACEHOLDER"
  fi
done

echo ""
echo "Manifest saved to: $MANIFEST_FILE"
echo ""

# ---------------------------------------------------------------------------
# Final status
# ---------------------------------------------------------------------------
if [[ $VERIFY_FAILURES -eq 0 ]]; then
  echo -e "${GREEN}${BOLD}✔ All secrets configured and verified.${NC}"
  echo ""
  echo "Next: bash scripts/03_first_deploy.sh"
else
  echo -e "${RED}${BOLD}✘ $VERIFY_FAILURES secret(s) failed verification — fix before deploying.${NC}"
  exit 1
fi
