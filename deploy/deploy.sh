#!/usr/bin/env bash
# Deploy Astra Cloud Run backend service
set -euo pipefail

PROJECT_ID="${1:-$(gcloud config get-value project)}"
REGION="${2:-us-central1}"
SERVICE_NAME="astra-backend"

echo "Deploying ${SERVICE_NAME} to Google Cloud Run (Project: ${PROJECT_ID}, Region: ${REGION})..."

# 1. Build and push container to Google Artifact Registry / Container Registry
gcloud builds submit --tag "gcr.io/${PROJECT_ID}/${SERVICE_NAME}:latest" .

# 2. Deploy Cloud Run service with min-instances=1 for fast hackathon demo response
gcloud run deploy "${SERVICE_NAME}" \
  --image "gcr.io/${PROJECT_ID}/${SERVICE_NAME}:latest" \
  --platform managed \
  --region "${REGION}" \
  --allow-unauthenticated \
  --min-instances 1 \
  --max-instances 10 \
  --cpu 2 \
  --memory 2Gi \
  --timeout 300 \
  --set-env-vars "ASTRA_ENV=prod,ASTRA_PERSISTENCE_BACKEND=FIRESTORE,FIRESTORE_PROJECT_ID=${PROJECT_ID}"

echo "Deployment complete! Service URL:"
gcloud run services describe "${SERVICE_NAME}" --platform managed --region "${REGION}" --format 'value(status.url)'
