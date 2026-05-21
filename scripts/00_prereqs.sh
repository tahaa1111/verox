#!/usr/bin/env bash
# =============================================================================
# Medibox — Pre-flight Prerequisites Check
# Run this BEFORE any other script.
#
# Usage:
#   bash scripts/00_prereqs.sh
#   # or source a .env.deploy file first:
#   set -a && source .env.deploy && set +a && bash scripts/00_prereqs.sh
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

ok()     { echo -e "${GREEN}  ✔${NC}  $*"; }
fail()   { echo -e "${RED}  ✘${NC}  $*"; FAILED=$((FAILED + 1)); }
warn()   { echo -e "${YELLOW}  ⚠${NC}  $*"; WARNINGS=$((WARNINGS + 1)); }
info()   { echo -e "${BLUE}  ℹ${NC}  $*"; }
header() { echo -e "\n${BOLD}${BLUE}── $* ──${NC}"; }

FAILED=0; WARNINGS=0

echo -e "${BOLD}Medibox GCP Deployment — Pre-flight Check${NC}"
echo "================================================"

# ---------------------------------------------------------------------------
# 1. Load .env.deploy if present
# ---------------------------------------------------------------------------
header "Environment"
if [[ -f ".env.deploy" ]]; then
  set -a && source ".env.deploy" && set +a
  ok "Loaded .env.deploy"
else
  warn ".env.deploy not found — checking environment variables directly"
  info "Copy .env.deploy.example to .env.deploy and fill in your values"
fi

# ---------------------------------------------------------------------------
# 2. Required environment variables
# ---------------------------------------------------------------------------
header "Required Variables"
for var in PROJECT_ID REGION BILLING_ACCOUNT REPO; do
  if [[ -n "${!var:-}" ]]; then
    ok "$var = ${!var}"
  else
    fail "$var is not set (add to .env.deploy or export before running)"
  fi
done

for var in FIREBASE_ADMIN_JSON_PATH NOTIFICATION_EMAIL; do
  if [[ -n "${!var:-}" ]]; then
    ok "$var = ${!var}"
  else
    warn "$var is not set (optional — needed for full setup)"
  fi
done

# ---------------------------------------------------------------------------
# 3. gcloud CLI — version >= 460
# ---------------------------------------------------------------------------
header "gcloud CLI"
MIN_GCLOUD=460
if ! command -v gcloud &>/dev/null; then
  fail "gcloud is not installed"
  info "Install: https://cloud.google.com/sdk/docs/install"
else
  RAW=$(gcloud version 2>/dev/null | grep 'Google Cloud SDK' | grep -oE '[0-9]+' | head -1 || echo "0")
  if [[ "$RAW" -ge "$MIN_GCLOUD" ]]; then
    ok "gcloud version $RAW (>= $MIN_GCLOUD required)"
  else
    fail "gcloud version $RAW is too old — minimum $MIN_GCLOUD required"
    info "Run: gcloud components update"
  fi

  ACTIVE=$(gcloud auth list --filter=status:ACTIVE --format="value(account)" 2>/dev/null | head -1 || echo "")
  if [[ -n "$ACTIVE" ]]; then
    ok "Authenticated as: $ACTIVE"
  else
    fail "Not authenticated — run: gcloud auth login"
  fi

  ADC="${HOME}/.config/gcloud/application_default_credentials.json"
  if [[ -f "$ADC" ]]; then
    ok "Application Default Credentials found"
  else
    fail "Application Default Credentials missing — run: gcloud auth application-default login"
  fi

  CUR_PROJECT=$(gcloud config get-value project 2>/dev/null || echo "")
  if [[ -n "${PROJECT_ID:-}" && -n "$CUR_PROJECT" && "$CUR_PROJECT" != "$PROJECT_ID" ]]; then
    warn "Active project ($CUR_PROJECT) differs from PROJECT_ID ($PROJECT_ID)"
    info "Run: gcloud config set project $PROJECT_ID"
  fi

  for comp in beta alpha; do
    if gcloud components list --filter="id=$comp AND state.name=Installed" \
         --format="value(id)" 2>/dev/null | grep -q "$comp"; then
      ok "gcloud $comp component installed"
    else
      warn "gcloud $comp not installed — run: gcloud components install $comp"
    fi
  done
fi

# ---------------------------------------------------------------------------
# 4. Docker
# ---------------------------------------------------------------------------
header "Docker"
if ! command -v docker &>/dev/null; then
  fail "docker not installed — see https://docs.docker.com/engine/install/"
else
  VER=$(docker version --format '{{.Client.Version}}' 2>/dev/null || echo "unknown")
  ok "Docker $VER installed"
  if docker info &>/dev/null; then
    ok "Docker daemon is running"
  else
    fail "Docker daemon is not running — start Docker Desktop or systemctl start docker"
  fi
fi

# ---------------------------------------------------------------------------
# 5. Python >= 3.11
# ---------------------------------------------------------------------------
header "Python"
PY_OK=false
for cmd in python3 python; do
  if command -v "$cmd" &>/dev/null; then
    VER=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')" 2>/dev/null || echo "0.0.0")
    MAJOR=$(echo "$VER" | cut -d. -f1)
    MINOR=$(echo "$VER" | cut -d. -f2)
    if [[ "$MAJOR" -ge 3 && "$MINOR" -ge 11 ]]; then
      ok "Python $VER ($cmd)"
      PY_OK=true
      break
    else
      warn "Python $VER ($cmd) is below 3.11"
    fi
  fi
done
$PY_OK || fail "Python 3.11+ not found — install from python.org or use pyenv"

if command -v pip3 &>/dev/null || command -v pip &>/dev/null; then
  ok "pip is available"
else
  fail "pip is not installed"
fi

# ---------------------------------------------------------------------------
# 6. Billing account
# ---------------------------------------------------------------------------
header "Billing Account"
if [[ -n "${BILLING_ACCOUNT:-}" ]]; then
  if gcloud billing accounts describe "$BILLING_ACCOUNT" &>/dev/null; then
    NAME=$(gcloud billing accounts describe "$BILLING_ACCOUNT" \
           --format="value(displayName)" 2>/dev/null || echo "unknown")
    ok "Billing account '$NAME' ($BILLING_ACCOUNT) is accessible"
  else
    fail "Billing account $BILLING_ACCOUNT not accessible — run: gcloud billing accounts list"
  fi
else
  warn "BILLING_ACCOUNT not set — run: gcloud billing accounts list"
fi

# ---------------------------------------------------------------------------
# 7. PROJECT_ID format
# ---------------------------------------------------------------------------
header "Project ID"
if [[ -n "${PROJECT_ID:-}" ]]; then
  if [[ ${#PROJECT_ID} -ge 6 && ${#PROJECT_ID} -le 30 && "$PROJECT_ID" =~ ^[a-z][a-z0-9-]*[a-z0-9]$ ]]; then
    ok "PROJECT_ID '$PROJECT_ID' format is valid"
    if gcloud projects describe "$PROJECT_ID" &>/dev/null; then
      ok "Project '$PROJECT_ID' already exists (will be reused)"
    else
      ok "Project '$PROJECT_ID' does not exist yet — will be created"
    fi
  else
    fail "PROJECT_ID '$PROJECT_ID' is invalid: must be 6-30 chars, lowercase, start with letter, only letters/digits/hyphens"
  fi
fi

# ---------------------------------------------------------------------------
# 8. Required local files
# ---------------------------------------------------------------------------
header "Required Local Files"
for f in "referances/drug_dict.json" "referances/drug_registry.json"; do
  if [[ -f "$f" ]]; then
    BYTES=$(wc -c < "$f" 2>/dev/null || echo "0")
    ok "$f (${BYTES} bytes)"
  else
    fail "$f is missing — required for drug normalization"
  fi
done

if [[ -n "${FIREBASE_ADMIN_JSON_PATH:-}" ]]; then
  if [[ -f "$FIREBASE_ADMIN_JSON_PATH" ]]; then
    if python3 -c "import json; d=json.load(open('$FIREBASE_ADMIN_JSON_PATH')); assert 'project_id' in d and 'private_key' in d" 2>/dev/null; then
      ok "Firebase admin JSON is valid: $FIREBASE_ADMIN_JSON_PATH"
    else
      fail "Firebase admin JSON at $FIREBASE_ADMIN_JSON_PATH is invalid or missing required fields"
    fi
  else
    fail "FIREBASE_ADMIN_JSON_PATH=$FIREBASE_ADMIN_JSON_PATH does not exist"
  fi
else
  warn "FIREBASE_ADMIN_JSON_PATH not set — you will be prompted during 02_setup_secrets.sh"
fi

# ---------------------------------------------------------------------------
# 9. Network connectivity
# ---------------------------------------------------------------------------
header "Network"
if curl -sf --max-time 5 "https://cloud.google.com" &>/dev/null; then
  ok "Reached cloud.google.com"
else
  fail "Cannot reach cloud.google.com — check internet connection"
fi
if curl -sf --max-time 5 "https://gcr.io" &>/dev/null; then
  ok "Reached gcr.io (container registry)"
else
  warn "Cannot reach gcr.io — container builds may fail"
fi

# ---------------------------------------------------------------------------
# 10. Optional tools
# ---------------------------------------------------------------------------
header "Optional Tools"
command -v jq      &>/dev/null && ok "jq available"      || warn "jq not installed — apt install jq / brew install jq"
command -v psql    &>/dev/null && ok "psql available"    || info "psql optional (for direct DB inspection)"
command -v openssl &>/dev/null && ok "openssl available" || warn "openssl not installed — needed for manual secret generation"
command -v curl    &>/dev/null && ok "curl available"    || fail "curl is required"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "================================================"
echo -e "${BOLD}Pre-flight Summary${NC}"
echo "================================================"
if [[ $FAILED -eq 0 && $WARNINGS -eq 0 ]]; then
  echo -e "${GREEN}${BOLD}✔ All checks passed. Ready to deploy.${NC}"
  echo ""
  echo "Next: bash scripts/01_setup_project.sh"
elif [[ $FAILED -eq 0 ]]; then
  echo -e "${YELLOW}${BOLD}⚠ $WARNINGS warning(s) — review above, then proceed with caution.${NC}"
  echo ""
  echo "Next: bash scripts/01_setup_project.sh"
else
  echo -e "${RED}${BOLD}✘ $FAILED error(s), $WARNINGS warning(s) — fix errors before proceeding.${NC}"
  exit 1
fi
