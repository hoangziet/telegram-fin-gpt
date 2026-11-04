#!/usr/bin/env bash
set -euo pipefail

: "${AZURE_SUBSCRIPTION_ID:?Set the owner-selected Azure subscription.}"
: "${TARGET_RESOURCE_GROUP:?Set the PostgreSQL resource group.}"
: "${NAME_PREFIX:?Set the FinGPT workload prefix.}"
: "${ENVIRONMENT_NAME:?Set the deployed environment.}"
: "${BACKUP_NAME:?Set the exact named on-demand backup.}"

server_name="${NAME_PREFIX}-${ENVIRONMENT_NAME}-pg"
az postgres flexible-server backup list \
  --subscription "$AZURE_SUBSCRIPTION_ID" \
  --resource-group "$TARGET_RESOURCE_GROUP" \
  --server-name "$server_name" \
  --output json \
  | python3 infra/verify_backup.py --name "$BACKUP_NAME"
