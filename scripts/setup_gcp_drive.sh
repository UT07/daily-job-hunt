#!/usr/bin/env bash
# Setup Google Drive API service account for daily-job-hunt pipeline.
# Run this on a machine with gcloud CLI installed and authenticated.
#
# Usage:
#   chmod +x scripts/setup_gcp_drive.sh
#   ./scripts/setup_gcp_drive.sh [PROJECT_ID]
#
# What this does:
#   1. Creates (or reuses) a GCP project
#   2. Enables the Google Drive API
#   3. Creates a service account
#   4. Downloads the JSON key as google_credentials.json
#   5. Prints the base64-encoded key for GitHub Actions secrets

set -euo pipefail

PROJECT_ID="${1:-daily-job-hunt}"
SA_NAME="drive-uploader"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
KEY_FILE="google_credentials.json"

echo "=== Google Drive API Setup for daily-job-hunt ==="
echo "Project: ${PROJECT_ID}"
echo ""

# Step 1: Ensure project exists and is selected
echo "[1/5] Setting project..."
if ! gcloud projects describe "${PROJECT_ID}" &>/dev/null; then
    echo "  Creating project ${PROJECT_ID}..."
    gcloud projects create "${PROJECT_ID}" --name="Daily Job Hunt"
fi
gcloud config set project "${PROJECT_ID}"

# Step 2: Enable Drive API
echo "[2/5] Enabling Google Drive API..."
gcloud services enable drive.googleapis.com

# Step 3: Create service account (skip if exists)
echo "[3/5] Creating service account..."
if ! gcloud iam service-accounts describe "${SA_EMAIL}" &>/dev/null 2>&1; then
    gcloud iam service-accounts create "${SA_NAME}" \
        --display-name="Drive Uploader for Job Hunt Pipeline"
    echo "  Created: ${SA_EMAIL}"
else
    echo "  Already exists: ${SA_EMAIL}"
fi

# Step 4: Download JSON key
echo "[4/5] Generating service account key..."
if [ -f "${KEY_FILE}" ]; then
    echo "  ${KEY_FILE} already exists. Backing up..."
    mv "${KEY_FILE}" "${KEY_FILE}.bak.$(date +%s)"
fi
gcloud iam service-accounts keys create "${KEY_FILE}" \
    --iam-account="${SA_EMAIL}"
echo "  Saved to: ${KEY_FILE}"

# Step 5: Base64 encode for GitHub Actions
echo "[5/5] Base64-encoded key for GitHub Actions:"
echo ""
echo "──────────────────────────────────────────"
echo "Add this as GOOGLE_CREDENTIALS_JSON secret:"
echo "──────────────────────────────────────────"
base64 -w 0 < "${KEY_FILE}"
echo ""
echo "──────────────────────────────────────────"
echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "  1. Copy the base64 string above"
echo "  2. Go to: GitHub repo → Settings → Secrets → Actions"
echo "  3. Add secret: GOOGLE_CREDENTIALS_JSON = <paste>"
echo "  4. The pipeline will auto-detect the credentials and upload to Drive"
echo ""
echo "IMPORTANT: Do NOT commit google_credentials.json to git!"
echo "  It's already in .gitignore."
