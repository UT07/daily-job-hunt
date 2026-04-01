#!/bin/bash
# Enable all EventBridge schedules for the Step Functions pipeline.
# Run this after shadow mode testing is complete.
#
# Usage: bash scripts/enable_schedules.sh
# Undo:  bash scripts/disable_schedules.sh

set -euo pipefail
REGION="eu-west-1"

echo "Enabling EventBridge schedules..."

for rule in naukribaba-daily-pipeline naukribaba-expiry-check naukribaba-stale-nudge naukribaba-followup-reminder; do
  echo "  Enabling $rule..."
  aws events enable-rule --name "$rule" --region "$REGION"
done

echo ""
echo "All schedules enabled:"
aws events list-rules --name-prefix naukribaba --region "$REGION" \
  --query "Rules[].{Name:Name, State:State, Schedule:ScheduleExpression}" \
  --output table

echo ""
echo "Done! The Step Functions pipeline is now live."
echo "Monitor: https://eu-west-1.console.aws.amazon.com/states/home?region=eu-west-1#/statemachines"
