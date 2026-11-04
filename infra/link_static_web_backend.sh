#!/usr/bin/env bash
set -euo pipefail

: "${AZURE_SUBSCRIPTION_ID:?Set the selected subscription ID.}"
: "${TARGET_RESOURCE_GROUP:?Set the target resource group.}"
: "${STATIC_WEB_APP_NAME:?Set the Static Web App name.}"
: "${API_CONTAINER_APP_NAME:?Set the API Container App name.}"

az account set --subscription "$AZURE_SUBSCRIPTION_ID"
active_subscription_id="$(az account show --query id --output tsv)"
if [[ "${active_subscription_id,,}" != "${AZURE_SUBSCRIPTION_ID,,}" ]]; then
  echo "Active subscription ID does not match AZURE_SUBSCRIPTION_ID." >&2
  exit 1
fi

api_resource_id="$(az containerapp show \
  --subscription "$AZURE_SUBSCRIPTION_ID" \
  --resource-group "$TARGET_RESOURCE_GROUP" \
  --name "$API_CONTAINER_APP_NAME" \
  --query id --output tsv)"

az staticwebapp backends link \
  --subscription "$AZURE_SUBSCRIPTION_ID" \
  --resource-group "$TARGET_RESOURCE_GROUP" \
  --name "$STATIC_WEB_APP_NAME" \
  --backend-resource-id "$api_resource_id"

az staticwebapp show \
  --subscription "$AZURE_SUBSCRIPTION_ID" \
  --resource-group "$TARGET_RESOURCE_GROUP" \
  --name "$STATIC_WEB_APP_NAME" \
  --query defaultHostname --output tsv
