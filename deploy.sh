#!/bin/bash
# ============================================================
# deploy.sh — One-command deployment to Google Cloud Run
# ============================================================
#
# BEFORE RUNNING THIS SCRIPT:
# 1. Install the Google Cloud CLI: https://cloud.google.com/sdk/docs/install
# 2. Log in: gcloud auth login
# 3. Edit config.yaml with your project_id, bucket name, and email settings
# 4. Set up your API keys in Secret Manager (see MAINTENANCE_GUIDE.md)
#
# TO RUN:
#   chmod +x deploy.sh    (first time only — makes the script executable)
#   ./deploy.sh
# ============================================================

set -e  # Stop on any error

# ── Read settings from config.yaml ────────────────────────────────────────────

# Requires Python (available by default on macOS)
PROJECT_ID=$(python3 -c "import yaml; c=yaml.safe_load(open('config.yaml')); print(c['google_cloud']['project_id'])")
REGION=$(python3 -c "import yaml; c=yaml.safe_load(open('config.yaml')); print(c['google_cloud']['region'])")
BUCKET=$(python3 -c "import yaml; c=yaml.safe_load(open('config.yaml')); print(c['google_cloud']['reports_bucket'])")

SERVICE_NAME=$(python3 -c "import yaml; c=yaml.safe_load(open('config.yaml')); print(c['google_cloud']['service_name'])")
SEC_AGENT=$(python3 -c "import yaml; c=yaml.safe_load(open('config.yaml')); print(c['google_cloud']['sec_user_agent'])")
IMAGE_NAME="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"

echo ""
echo "=========================================="
echo "  FinResearchAgent — Deploying"
echo "=========================================="
echo "  Project:  ${PROJECT_ID}"
echo "  Region:   ${REGION}"
echo "  Service:  ${SERVICE_NAME}"
echo "  Image:    ${IMAGE_NAME}"
echo "  Bucket:   ${BUCKET}"
echo "=========================================="
echo ""

# Confirm before deploying
read -p "Deploy to Google Cloud? (y/N) " confirm
if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
  echo "Deployment cancelled."
  exit 0
fi

# ── Step 1: Set the active project ────────────────────────────────────────────
echo ""
echo "Step 1/6: Setting Google Cloud project..."
gcloud config set project "${PROJECT_ID}"

# ── Step 2: Enable required Google Cloud APIs ──────────────────────────────────
echo ""
echo "Step 2/6: Enabling required APIs (this takes ~1 minute first time)..."
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  aiplatform.googleapis.com \
  secretmanager.googleapis.com \
  cloudscheduler.googleapis.com \
  storage.googleapis.com \
  --quiet

# ── Step 3: Grant Cloud Run service account access to Secret Manager ──────────
echo ""
echo "Step 3/7: Granting Secret Manager access to the Cloud Run service account..."
PROJECT_NUMBER=$(gcloud projects describe "${PROJECT_ID}" --format="value(projectNumber)")
SERVICE_ACCOUNT="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${SERVICE_ACCOUNT}" \
  --role="roles/secretmanager.secretAccessor" \
  --quiet
echo "  Granted roles/secretmanager.secretAccessor to ${SERVICE_ACCOUNT}"

# ── Step 4: Create Cloud Storage bucket (if it doesn't exist) ─────────────────
echo ""
echo "Step 4/7: Creating Cloud Storage bucket (if needed)..."
gsutil mb -p "${PROJECT_ID}" -l "${REGION}" "gs://${BUCKET}" 2>/dev/null || \
  echo "  Bucket gs://${BUCKET} already exists — skipping."

# ── Step 5: Build and push the Docker image ────────────────────────────────────
echo ""
echo "Step 5/7: Building and pushing Docker image..."
gcloud builds submit --tag "${IMAGE_NAME}" .

# ── Step 6: Deploy to Cloud Run ────────────────────────────────────────────────
echo ""
echo "Step 6/7: Deploying to Cloud Run..."

# Retrieve Secret Manager secret names from config.yaml
AV_SECRET=$(python3 -c "import yaml; c=yaml.safe_load(open('config.yaml')); print(c['secrets']['alpha_vantage_key'])")
FRED_SECRET=$(python3 -c "import yaml; c=yaml.safe_load(open('config.yaml')); print(c['secrets']['fred_api_key'])")
FINNHUB_SECRET=$(python3 -c "import yaml; c=yaml.safe_load(open('config.yaml')); print(c['secrets']['finnhub_api_key'])")
FMP_SECRET=$(python3 -c "import yaml; c=yaml.safe_load(open('config.yaml')); print(c['secrets']['fmp_api_key'])")
CORE_SECRET=$(python3 -c "import yaml; c=yaml.safe_load(open('config.yaml')); print(c['secrets']['core_api_key'])")
SS_SECRET=$(python3 -c "import yaml; c=yaml.safe_load(open('config.yaml')); print(c['secrets']['semantic_scholar_api_key'])")
POLYGON_SECRET=$(python3 -c "import yaml; c=yaml.safe_load(open('config.yaml')); print(c['secrets']['polygon_api_key'])")
NEWS_SECRET=$(python3 -c "import yaml; c=yaml.safe_load(open('config.yaml')); print(c['secrets']['news_api_key'])")
OPENFIGI_SECRET=$(python3 -c "import yaml; c=yaml.safe_load(open('config.yaml')); print(c['secrets']['openfigi_api_key'])")
GOOGLE_AI_SECRET=$(python3 -c "import yaml; c=yaml.safe_load(open('config.yaml')); print(c['secrets']['google_ai_api_key'])")
SEARCH_INTERVAL=$(python3 -c "import yaml; c=yaml.safe_load(open('config.yaml')); print(c.get('search', {}).get('min_interval_seconds', 2.0))")
MODEL_REGION=$(python3 -c "import yaml; c=yaml.safe_load(open('config.yaml')); print(c['google_cloud'].get('model_region', 'global'))")

gcloud run deploy "${SERVICE_NAME}" \
  --image "${IMAGE_NAME}" \
  --platform managed \
  --region "${REGION}" \
  --allow-unauthenticated \
  --memory 2Gi \
  --cpu 2 \
  --timeout 3600 \
  --max-instances 5 \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=${PROJECT_ID},REPORTS_BUCKET=${BUCKET},GOOGLE_GENAI_USE_VERTEXAI=1,GOOGLE_CLOUD_LOCATION=${MODEL_REGION},SEC_USER_AGENT=${SEC_AGENT},SEARCH_MIN_INTERVAL=${SEARCH_INTERVAL}" \
  --set-secrets "ALPHA_VANTAGE_KEY=${AV_SECRET}:latest,FRED_API_KEY=${FRED_SECRET}:latest,FINNHUB_API_KEY=${FINNHUB_SECRET}:latest,FMP_API_KEY=${FMP_SECRET}:latest,CORE_API_KEY=${CORE_SECRET}:latest,SEMANTIC_SCHOLAR_API_KEY=${SS_SECRET}:latest,POLYGON_API_KEY=${POLYGON_SECRET}:latest,NEWS_API_KEY=${NEWS_SECRET}:latest,OPENFIGI_API_KEY=${OPENFIGI_SECRET}:latest,GOOGLE_AI_API_KEY=${GOOGLE_AI_SECRET}:latest" \
  --quiet

# ── Step 7: Get the service URL ────────────────────────────────────────────────
echo ""
echo "Step 7/7: Retrieving service URL..."
SERVICE_URL=$(gcloud run services describe "${SERVICE_NAME}" \
  --platform managed \
  --region "${REGION}" \
  --format "value(status.url)")

echo ""
echo "=========================================="
echo "  Deployment complete!"
echo "=========================================="
echo ""
echo "  Your research agent is live at:"
echo "  ${SERVICE_URL}"
echo ""
echo "  Open the URL above in your browser to submit research requests."
echo ""
echo "  NEXT STEPS:"
echo "  1. Visit ${SERVICE_URL}/health to verify the service is running"
echo "  2. Set up Cloud Scheduler for automated runs (see MAINTENANCE_GUIDE.md)"
echo "  3. Test with a sample ticker: open ${SERVICE_URL} and enter AAPL"
echo ""
echo "  To redeploy after changes: run ./deploy.sh again"
echo "=========================================="
