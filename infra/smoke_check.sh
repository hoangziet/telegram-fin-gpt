#!/usr/bin/env bash
set -euo pipefail

: "${API_BASE_URL:?Set API_BASE_URL to the reviewed HTTPS endpoint.}"
: "${AZURE_SUBSCRIPTION_ID:?Set the owner-selected Azure subscription.}"
: "${TARGET_RESOURCE_GROUP:?Set the deployed resource group.}"
: "${NAME_PREFIX:?Set the FinGPT workload prefix.}"
: "${ENVIRONMENT_NAME:?Set the deployed environment.}"

curl --fail --silent --show-error --max-time 20 "$API_BASE_URL/health/live"
curl --fail --silent --show-error --max-time 20 "$API_BASE_URL/health/ready"
curl --fail --silent --show-error --max-time 20 "$API_BASE_URL/" \
  | grep -Eiq '<html([[:space:]>])'

worker_name="${NAME_PREFIX}-${ENVIRONMENT_NAME}-worker"
revision_name="$(az containerapp show \
  --subscription "$AZURE_SUBSCRIPTION_ID" \
  --resource-group "$TARGET_RESOURCE_GROUP" \
  --name "$worker_name" \
  --query properties.latestRevisionName --output tsv)"
[[ -n "$revision_name" && "$revision_name" != None ]]

for attempt in {1..12}; do
  revision_state="$(az containerapp revision show \
    --subscription "$AZURE_SUBSCRIPTION_ID" \
    --resource-group "$TARGET_RESOURCE_GROUP" \
    --name "$worker_name" \
    --revision "$revision_name" \
    --query '[properties.healthState,properties.runningState]' --output tsv)"
  printf 'Worker revision %s state: %s\n' "$revision_name" "$revision_state"
  if [[ "$revision_state" == $'Healthy\tRunning' \
    || "$revision_state" == $'Healthy\tRunning (at max)' ]]; then
    break
  fi
  if (( attempt == 12 )); then
    echo "Worker revision did not become healthy and running." >&2
    exit 1
  fi
  sleep 15
done

printf '\nAPI endpoints, web shell, and worker revision passed at %s\n' "$API_BASE_URL"
