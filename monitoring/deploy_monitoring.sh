#!/usr/bin/env bash
# Deploy Cloud Monitoring dashboards and alert policies.
# Run after 01_setup_project.sh and after the API is deployed.
#
# Usage:
#   export GCP_PROJECT_ID=your-project-id
#   export NOTIFICATION_EMAIL=oncall@example.com
#   bash monitoring/deploy_monitoring.sh

set -euo pipefail

PROJECT_ID="${GCP_PROJECT_ID:?Set GCP_PROJECT_ID}"
NOTIFICATION_EMAIL="${NOTIFICATION_EMAIL:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Medibox: Deploying Cloud Monitoring resources ==="
echo "Project: $PROJECT_ID"

# ---------------------------------------------------------------------------
# Create email notification channel (if email provided)
# ---------------------------------------------------------------------------
NOTIFICATION_CHANNEL=""
if [[ -n "$NOTIFICATION_EMAIL" ]]; then
  echo "--- Creating email notification channel for $NOTIFICATION_EMAIL ---"
  CHANNEL_JSON=$(gcloud alpha monitoring channels create \
    --display-name="Medibox Oncall" \
    --type=email \
    --channel-labels="email_address=${NOTIFICATION_EMAIL}" \
    --project="$PROJECT_ID" \
    --format=json 2>/dev/null || echo "")
  if [[ -n "$CHANNEL_JSON" ]]; then
    NOTIFICATION_CHANNEL=$(echo "$CHANNEL_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['name'])")
    echo "Notification channel: $NOTIFICATION_CHANNEL"
  fi
fi

# ---------------------------------------------------------------------------
# Create custom log-based metrics (used by dashboards + alerts)
# ---------------------------------------------------------------------------
echo "--- Creating custom log-based metrics ---"

create_log_metric() {
  local name="$1"
  local filter="$2"
  gcloud logging metrics create "$name" \
    --description="Medibox: $name" \
    --log-filter="$filter" \
    --project="$PROJECT_ID" 2>/dev/null \
    || gcloud logging metrics update "$name" \
       --log-filter="$filter" \
       --project="$PROJECT_ID" 2>/dev/null \
    || echo "  [warn] Could not create/update metric $name"
  echo "  metric: $name"
}

create_log_metric "medibox_confidence_score" \
  'resource.type="cloud_run_revision" jsonPayload.event="inference_complete" jsonPayload.confidence_score!=""'

create_log_metric "medibox_low_confidence_count" \
  'resource.type="cloud_run_revision" jsonPayload.event="inference_complete" jsonPayload.confidence_score<"0.5"'

create_log_metric "medibox_formulary_miss_count" \
  'resource.type="cloud_run_revision" jsonPayload.formulary_miss="true"'

create_log_metric "medibox_correction_count" \
  'resource.type="cloud_run_revision" jsonPayload.event="correction_submitted"'

create_log_metric "medibox_json_repair_count" \
  'resource.type="cloud_run_revision" jsonPayload.event="json_repaired"'

create_log_metric "medibox_hallucination_count" \
  'resource.type="cloud_run_revision" jsonPayload.event="hallucination_detected"'

create_log_metric "medibox_review_queue_size" \
  'resource.type="cloud_run_revision" jsonPayload.event="review_queue_update"'

create_log_metric "medibox_drug_normalize_hit_rate" \
  'resource.type="cloud_run_revision" jsonPayload.event="drug_normalize_result" jsonPayload.hit="true"'

create_log_metric "medibox_grid_compose_ms" \
  'resource.type="cloud_run_revision" jsonPayload.event="grid_composed"'

create_log_metric "medibox_eval_drug_f1" \
  'resource.type="cloud_run_revision" jsonPayload.event="eval_complete"'

create_log_metric "medibox_eval_json_validity" \
  'resource.type="cloud_run_revision" jsonPayload.event="eval_complete"'

create_log_metric "medibox_eval_rare_drug_acc" \
  'resource.type="cloud_run_revision" jsonPayload.event="eval_complete"'

create_log_metric "medibox_audit_write_count" \
  'resource.type="cloud_run_revision" jsonPayload.event="audit_log_write"'

# ---------------------------------------------------------------------------
# Deploy dashboards
# ---------------------------------------------------------------------------
echo "--- Deploying dashboards ---"
for dashboard_file in "$SCRIPT_DIR/dashboards/"*.json; do
  name=$(basename "$dashboard_file" .json)
  echo "  Deploying dashboard: $name"
  gcloud monitoring dashboards create \
    --config-from-file="$dashboard_file" \
    --project="$PROJECT_ID" \
    2>/dev/null && echo "  Created: $name" || echo "  [warn] Dashboard may already exist: $name (update manually)"
done

# ---------------------------------------------------------------------------
# Deploy alert policies
# ---------------------------------------------------------------------------
echo "--- Deploying alert policies ---"
POLICIES_FILE="$SCRIPT_DIR/alerts/alert_policies.json"

# Read alert policies array and create each one
POLICY_COUNT=$(python3 -c "import json; data=json.load(open('$POLICIES_FILE')); print(len(data))")
echo "  Found $POLICY_COUNT alert policies"

python3 - <<PYEOF
import json, subprocess, sys

with open('$POLICIES_FILE') as f:
    policies = json.load(f)

project = '$PROJECT_ID'
channel = '$NOTIFICATION_CHANNEL'

for policy in policies:
    name = policy['displayName']
    # Inject notification channel if we have one
    if channel:
        policy['notificationChannels'] = [channel]

    # Write temp file for this policy
    import tempfile, os
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as tmp:
        json.dump(policy, tmp, indent=2)
        tmp_path = tmp.name

    try:
        result = subprocess.run(
            ['gcloud', 'alpha', 'monitoring', 'policies', 'create',
             f'--policy-from-file={tmp_path}',
             f'--project={project}'],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            print(f'  Created: {name}')
        else:
            print(f'  [warn] Could not create policy "{name}": {result.stderr.strip()[:100]}')
    finally:
        os.unlink(tmp_path)
PYEOF

# ---------------------------------------------------------------------------
# Budget alert (requires billing account)
# ---------------------------------------------------------------------------
echo ""
echo "--- Budget alerts must be configured manually ---"
echo "Go to: Cloud Console → Billing → Budgets & alerts → Create budget"
echo "Thresholds: \$250 (alert), \$350 (alert), \$500 (alert + Pub/Sub pause)"
echo ""
echo "=== Cloud Monitoring deployment complete ==="
echo "Dashboards: https://console.cloud.google.com/monitoring/dashboards?project=$PROJECT_ID"
echo "Alerts:     https://console.cloud.google.com/monitoring/alerting?project=$PROJECT_ID"
