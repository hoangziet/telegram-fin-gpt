#!/usr/bin/env bash
set -euo pipefail

: "${AZURE_SUBSCRIPTION_ID:?Set the owner-selected Azure subscription.}"
: "${TARGET_RESOURCE_GROUP:?Set the test resource group.}"
: "${NAME_PREFIX:?Set the FinGPT workload prefix.}"
: "${RESTORE_SERVER_NAME:?Set the test restore server name to clean up.}"
MODE="${MODE:-preview}"

az account set --subscription "$AZURE_SUBSCRIPTION_ID"
subscription_name="$(az account show --query name --output tsv)"
active_subscription_id="$(az account show --query id --output tsv)"
if [[ "${active_subscription_id,,}" != "${AZURE_SUBSCRIPTION_ID,,}" ]]; then
  echo "Active subscription ID does not match AZURE_SUBSCRIPTION_ID." >&2
  exit 1
fi

target_count="$(az postgres flexible-server list \
  --subscription "$AZURE_SUBSCRIPTION_ID" \
  --resource-group "$TARGET_RESOURCE_GROUP" \
  --query "[?name=='$RESTORE_SERVER_NAME'] | length(@)" --output tsv)"

server_exists_arg=()
server_state="NotFound"
if [[ "$target_count" != 0 ]]; then
  server_exists_arg=(--server-exists)
  server_state="$(az postgres flexible-server show \
    --subscription "$AZURE_SUBSCRIPTION_ID" \
    --resource-group "$TARGET_RESOURCE_GROUP" \
    --name "$RESTORE_SERVER_NAME" \
    --query state --output tsv)"
fi

cleanup_plan="$(python3 infra/validate_restore_cleanup.py \
  --name-prefix "$NAME_PREFIX" \
  --subscription-name "$subscription_name" \
  --resource-group "$TARGET_RESOURCE_GROUP" \
  --server-name "$RESTORE_SERVER_NAME" \
  --server-state "$server_state" \
  "${server_exists_arg[@]}")"

printf '%s\n' "$cleanup_plan"

if [[ "$MODE" == "preview" ]]; then
  printf '\nPreview complete: Server %s is verified for safe cleanup.\n' "$RESTORE_SERVER_NAME"
  exit 0
fi

if [[ "$MODE" == "apply" ]]; then
  if [[ "${CONFIRM_CLEANUP:-false}" != "true" ]]; then
    echo "Deletion requires explicit confirmation: set CONFIRM_CLEANUP=true." >&2
    exit 1
  fi
  printf 'Deleting temporary restore server %s...\n' "$RESTORE_SERVER_NAME"
  az postgres flexible-server delete \
    --subscription "$AZURE_SUBSCRIPTION_ID" \
    --resource-group "$TARGET_RESOURCE_GROUP" \
    --name "$RESTORE_SERVER_NAME" \
    --yes \
    --no-wait
  printf 'Deletion requested for %s.\n' "$RESTORE_SERVER_NAME"
fi
