#!/usr/bin/env bash
# ==============================================================================
# Ask-a-Friend MCP — Automated Google Cloud Run Deployment Script
# ==============================================================================
set -euo pipefail

PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null || echo "")}}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE_NAME:-ask-friend-mcp}"
SA_NAME="ask-friend-runner"

if [ -z "${PROJECT_ID}" ]; then
  echo "❌ ERROR: No GCP project ID provided."
  echo "Usage: ./deploy.sh <GCP_PROJECT_ID> (or set GOOGLE_CLOUD_PROJECT in your environment)"
  exit 1
fi

echo "🚀 Deploying Ask-a-Friend MCP to GCP Project: ${PROJECT_ID}..."

# 1. Enable Required GCP APIs
echo "📦 Enabling GCP Service APIs..."
gcloud services enable \
    run.googleapis.com \
    aiplatform.googleapis.com \
    secretmanager.googleapis.com \
    cloudbuild.googleapis.com \
    artifactregistry.googleapis.com \
    orgpolicy.googleapis.com \
    --project="${PROJECT_ID}"

# 1b. Configure Vertex AI Partner Model Features & Web Search Org Policies (if permitted)
echo "🛡️ Configuring Vertex AI Organization Policies (allowedPartnerModelFeatures & allowedModels)..."
TMP_PARTNER_POLICY=$(mktemp)
cat <<EOF > "${TMP_PARTNER_POLICY}"
name: projects/${PROJECT_ID}/policies/vertexai.allowedPartnerModelFeatures
spec:
  rules:
  - allowAll: true
EOF
gcloud org-policies set-policy "${TMP_PARTNER_POLICY}" --project="${PROJECT_ID}" --quiet 2>/dev/null || \
  echo "ℹ️  Note: Could not set vertexai.allowedPartnerModelFeatures automatically (requires roles/orgpolicy.policyAdmin)."
rm -f "${TMP_PARTNER_POLICY}"

TMP_MODELS_POLICY=$(mktemp)
cat <<EOF > "${TMP_MODELS_POLICY}"
name: projects/${PROJECT_ID}/policies/vertexai.allowedModels
spec:
  rules:
  - allowAll: true
EOF
gcloud org-policies set-policy "${TMP_MODELS_POLICY}" --project="${PROJECT_ID}" --quiet 2>/dev/null || true
rm -f "${TMP_MODELS_POLICY}"

# 2. Create Artifact Registry Repository (if not exists)
echo "📦 Ensuring Artifact Registry repository exists..."
gcloud artifacts repositories create ask-friend-repo \
    --repository-format=docker \
    --location="${REGION}" \
    --description="Docker repository for Ask-a-Friend MCP" \
    --project="${PROJECT_ID}" 2>/dev/null || true

# 3. Create Service Account & Grant IAM Permissions
echo "🔐 Setting up service account and IAM roles..."
gcloud iam service-accounts create "${SA_NAME}" \
    --display-name="Ask-a-Friend MCP Runner" \
    --project="${PROJECT_ID}" 2>/dev/null || true

SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

# Grant Vertex AI User role
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/aiplatform.user" \
    --quiet

# Grant Secret Manager Secret Accessor role
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/secretmanager.secretAccessor" \
    --quiet

# Grant Cloud Build SA & Compute SA permissions to deploy to Cloud Run and act as the service account
PROJECT_NUMBER=$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')
CB_SA="${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com"
COMPUTE_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${CB_SA}" \
    --role="roles/run.admin" \
    --quiet 2>/dev/null || true

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${COMPUTE_SA}" \
    --role="roles/run.admin" \
    --quiet 2>/dev/null || true

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${COMPUTE_SA}" \
    --role="roles/storage.admin" \
    --quiet 2>/dev/null || true

gcloud iam service-accounts add-iam-policy-binding "${SA_EMAIL}" \
    --member="serviceAccount:${CB_SA}" \
    --role="roles/iam.serviceAccountUser" \
    --project="${PROJECT_ID}" \
    --quiet 2>/dev/null || true

gcloud iam service-accounts add-iam-policy-binding "${SA_EMAIL}" \
    --member="serviceAccount:${COMPUTE_SA}" \
    --role="roles/iam.serviceAccountUser" \
    --project="${PROJECT_ID}" \
    --quiet 2>/dev/null || true

# 4. Ensure MCP_API_KEY secret exists
echo "🔑 Checking Secret Manager for mcp-api-key..."
if ! gcloud secrets describe mcp-api-key --project="${PROJECT_ID}" &>/dev/null; then
    GENERATED_KEY="aaf_$(openssl rand -hex 24)"
    echo "Creating new secret 'mcp-api-key' with generated key..."
    printf "%s" "${GENERATED_KEY}" | gcloud secrets create mcp-api-key \
        --data-file=- \
        --replication-policy="automatic" \
        --project="${PROJECT_ID}"
    echo "⚠️ Created secret 'mcp-api-key'. Save this key for client configs: ${GENERATED_KEY}"
fi

# 5. Submit Build to Cloud Build
echo "🏗️ Submitting build to Cloud Build..."
gcloud builds submit --config=cloudbuild.yaml --project="${PROJECT_ID}" \
    --substitutions="_REGION=${REGION},_SERVICE=${SERVICE},_SA=${SA_EMAIL}"

SERVICE_URL=$(gcloud run services describe "${SERVICE}" --region="${REGION}" --project="${PROJECT_ID}" --format='value(status.url)')

echo ""
echo "=============================================================================="
echo "🎉 Deployment Complete!"
echo "📡 Service URL:         ${SERVICE_URL}"
echo "🔌 MCP Streamable HTTP: ${SERVICE_URL}/mcp"
echo "⚡ MCP SSE Endpoint:    ${SERVICE_URL}/sse"
echo "📄 OpenAPI Schema:      ${SERVICE_URL}/openapi.yaml"
echo "=============================================================================="
