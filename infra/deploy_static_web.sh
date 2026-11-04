#!/usr/bin/env bash
set -euo pipefail

: "${AZURE_SUBSCRIPTION_ID:?Set the selected subscription ID.}"
: "${TARGET_RESOURCE_GROUP:?Set the target resource group.}"
: "${STATIC_WEB_APP_NAME:?Set the Static Web App name.}"

az account set --subscription "$AZURE_SUBSCRIPTION_ID"
active_subscription_id="$(az account show --query id --output tsv)"
if [[ "${active_subscription_id,,}" != "${AZURE_SUBSCRIPTION_ID,,}" ]]; then
  echo "Active subscription ID does not match AZURE_SUBSCRIPTION_ID." >&2
  exit 1
fi

corepack pnpm --dir web build
deployment_token="$(az staticwebapp secrets list \
  --subscription "$AZURE_SUBSCRIPTION_ID" \
  --resource-group "$TARGET_RESOURCE_GROUP" \
  --name "$STATIC_WEB_APP_NAME" \
  --query properties.apiKey --output tsv)"
if [[ -z "$deployment_token" || "$deployment_token" == "None" ]]; then
  echo "Could not retrieve the Static Web App deployment token." >&2
  exit 1
fi

npx --yes @azure/static-web-apps-cli deploy web/dist \
  --deployment-token "$deployment_token" \
  --env production

az staticwebapp show \
  --subscription "$AZURE_SUBSCRIPTION_ID" \
  --resource-group "$TARGET_RESOURCE_GROUP" \
  --name "$STATIC_WEB_APP_NAME" \
  --query defaultHostname --output tsv
