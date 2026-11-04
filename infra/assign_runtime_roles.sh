#!/usr/bin/env bash
set -euo pipefail

: "${AZURE_SUBSCRIPTION_ID:?Set the explicitly selected subscription ID.}"
: "${TARGET_RESOURCE_GROUP:?Set the target resource group.}"
: "${NAME_PREFIX:?Set the workload prefix.}"
: "${ENVIRONMENT_NAME:?Set the environment name.}"

az account set --subscription "$AZURE_SUBSCRIPTION_ID"
active_subscription_id="$(az account show --query id --output tsv)"
if [[ "${active_subscription_id,,}" != "${AZURE_SUBSCRIPTION_ID,,}" ]]; then
  echo "Active subscription ID does not match AZURE_SUBSCRIPTION_ID." >&2
  exit 1
fi

api_principal="$(az identity show -g "$TARGET_RESOURCE_GROUP" -n "${NAME_PREFIX}-${ENVIRONMENT_NAME}-api-id" --query principalId -o tsv)"
worker_principal="$(az identity show -g "$TARGET_RESOURCE_GROUP" -n "${NAME_PREFIX}-${ENVIRONMENT_NAME}-worker-id" --query principalId -o tsv)"
pull_principal="$(az identity show -g "$TARGET_RESOURCE_GROUP" -n "${NAME_PREFIX}-${ENVIRONMENT_NAME}-pull-id" --query principalId -o tsv)"
key_vault_id="$(az keyvault show -g "$TARGET_RESOURCE_GROUP" -n "${NAME_PREFIX}-${ENVIRONMENT_NAME}-kv" --query id -o tsv)"
storage_id="$(az storage account show -g "$TARGET_RESOURCE_GROUP" -n "${NAME_PREFIX}${ENVIRONMENT_NAME}evidence" --query id -o tsv)"
registry_id="$(az acr show -g "$TARGET_RESOURCE_GROUP" -n "${NAME_PREFIX}${ENVIRONMENT_NAME}acr" --query id -o tsv)"
service_bus_id="$(az servicebus namespace show -g "$TARGET_RESOURCE_GROUP" -n "${NAME_PREFIX}-${ENVIRONMENT_NAME}-servicebus" --query id -o tsv)"
foundry_id="$(az cognitiveservices account show -g "$TARGET_RESOURCE_GROUP" -n "${NAME_PREFIX}${ENVIRONMENT_NAME}ai" --query id -o tsv)"

queue_scope="${service_bus_id}/queues/fingpt-events"
container_scope="${storage_id}/blobServices/default/containers/evidence"

assign_if_missing() {
  local principal="$1" role="$2" scope="$3"
  local count
  count="$(az role assignment list --assignee-object-id "$principal" --scope "$scope" \
    --query "[?roleDefinitionName=='${role}'] | length(@)" --output tsv)"
  if [[ "$count" == "0" ]]; then
    az role assignment create \
      --assignee-object-id "$principal" \
      --assignee-principal-type ServicePrincipal \
      --role "$role" \
      --scope "$scope" \
      --output none
  fi
}

assign_if_missing "$api_principal" 'Key Vault Secrets User' "$key_vault_id"
assign_if_missing "$api_principal" 'Storage Blob Data Contributor' "$container_scope"
assign_if_missing "$api_principal" 'Cognitive Services OpenAI User' "$foundry_id"
assign_if_missing "$worker_principal" 'Key Vault Secrets User' "$key_vault_id"
assign_if_missing "$worker_principal" 'Azure Service Bus Data Sender' "$queue_scope"
assign_if_missing "$worker_principal" 'Azure Service Bus Data Receiver' "$queue_scope"
assign_if_missing "$pull_principal" 'AcrPull' "$registry_id"

printf 'Runtime role assignments verified for subscription %s.\n' "$active_subscription_id"
