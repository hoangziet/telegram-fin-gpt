#!/usr/bin/env bash
set -euo pipefail

: "${AZURE_SUBSCRIPTION_ID:?Set the owner-selected Azure subscription.}"
: "${TARGET_RESOURCE_GROUP:?Set the deployed resource group.}"
: "${NAME_PREFIX:?Set the FinGPT workload prefix.}"
: "${ENVIRONMENT_NAME:?Set the target environment (dev, test, prod).}"
MODE="${MODE:-preview}"

az account set --subscription "$AZURE_SUBSCRIPTION_ID"
subscription_name="$(az account show --query name --output tsv)"
active_subscription_id="$(az account show --query id --output tsv)"
if [[ "${active_subscription_id,,}" != "${AZURE_SUBSCRIPTION_ID,,}" ]]; then
  echo "Active subscription ID does not match AZURE_SUBSCRIPTION_ID." >&2
  exit 1
fi

api_name="${NAME_PREFIX}-${ENVIRONMENT_NAME}-api"
worker_name="${NAME_PREFIX}-${ENVIRONMENT_NAME}-worker"

target_api_revision="${TARGET_API_REVISION:-}"
if [[ -z "$target_api_revision" ]]; then
  # Find the previous active revision with healthy state
  query="[?properties.active && properties.trafficWeight == \`0\` && properties.healthState == 'Healthy'] | [-1].name"
  target_api_revision="$(az containerapp revision list \
    --subscription "$AZURE_SUBSCRIPTION_ID" \
    --resource-group "$TARGET_RESOURCE_GROUP" \
    --name "$api_name" \
    --query "$query" \
    --output tsv)"
fi

target_worker_revision="${TARGET_WORKER_REVISION:-}"
if [[ -z "$target_worker_revision" ]]; then
  target_worker_revision="$(az containerapp revision list \
    --subscription "$AZURE_SUBSCRIPTION_ID" \
    --resource-group "$TARGET_RESOURCE_GROUP" \
    --name "$worker_name" \
    --query "[?properties.provisioningState == 'Succeeded' && properties.healthState == 'Healthy'] | [-2].name" \
    --output tsv)"
fi

current_api_revision="$(az containerapp show \
  --subscription "$AZURE_SUBSCRIPTION_ID" \
  --resource-group "$TARGET_RESOURCE_GROUP" \
  --name "$api_name" \
  --query properties.latestRevisionName --output tsv)"

current_worker_revision="$(az containerapp show \
  --subscription "$AZURE_SUBSCRIPTION_ID" \
  --resource-group "$TARGET_RESOURCE_GROUP" \
  --name "$worker_name" \
  --query properties.latestRevisionName --output tsv)"

if [[ -z "$target_api_revision" || "$target_api_revision" == "None" ]]; then
  echo "No suitable previous API revision found for rollback." >&2
  exit 1
fi

if [[ -z "$target_worker_revision" || "$target_worker_revision" == "None" ]]; then
  echo "No suitable previous worker revision found for rollback." >&2
  exit 1
fi

printf 'Rollback plan:\n'
printf '  API app: %s\n' "$api_name"
printf '    Current: %s\n' "$current_api_revision"
printf '    Rollback target: %s (will receive 100%% traffic)\n' "$target_api_revision"
printf '  Worker app: %s\n' "$worker_name"
printf '    Current: %s\n' "$current_worker_revision"
printf '    Rollback target: %s (will be activated)\n' "$target_worker_revision"

if [[ "$MODE" == "preview" ]]; then
  printf '\nPreview complete. Run with MODE=apply to execute revision rollback.\n'
  exit 0
fi

if [[ "$MODE" == "apply" ]]; then
  printf '\nApplying API rollback to revision %s...\n' "$target_api_revision"
  az containerapp revision activate \
    --subscription "$AZURE_SUBSCRIPTION_ID" \
    --resource-group "$TARGET_RESOURCE_GROUP" \
    --name "$api_name" \
    --revision "$target_api_revision"

  az containerapp ingress traffic set \
    --subscription "$AZURE_SUBSCRIPTION_ID" \
    --resource-group "$TARGET_RESOURCE_GROUP" \
    --name "$api_name" \
    --revision-weight "${target_api_revision}=100"

  printf 'Applying worker rollback to revision %s...\n' "$target_worker_revision"
  az containerapp revision activate \
    --subscription "$AZURE_SUBSCRIPTION_ID" \
    --resource-group "$TARGET_RESOURCE_GROUP" \
    --name "$worker_name" \
    --revision "$target_worker_revision"

  if [[ "$current_worker_revision" != "$target_worker_revision" ]]; then
    az containerapp revision deactivate \
      --subscription "$AZURE_SUBSCRIPTION_ID" \
      --resource-group "$TARGET_RESOURCE_GROUP" \
      --name "$worker_name" \
      --revision "$current_worker_revision"
  fi

  printf 'Rollback applied successfully to API (%s) and Worker (%s).\n' \
    "$target_api_revision" "$target_worker_revision"
fi
