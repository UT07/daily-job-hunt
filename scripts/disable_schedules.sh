#!/bin/bash
# Disable all EventBridge schedules (rollback / pause).
# Usage: bash scripts/disable_schedules.sh

set -euo pipefail
REGION="eu-west-1"

echo "Disabling EventBridge schedules..."

for rule in naukribaba-daily-pipeline naukribaba-expiry-check naukribaba-stale-nudge naukribaba-followup-reminder; do
  echo "  Disabling $rule..."
  aws events disable-rule --name "$rule" --region "$REGION"
done

echo ""
echo "All schedules disabled."
aws events list-rules --name-prefix naukribaba --region "$REGION" \
  --query "Rules[].{Name:Name, State:State}" \
  --output table
