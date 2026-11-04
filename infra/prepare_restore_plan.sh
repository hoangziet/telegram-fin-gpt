#!/usr/bin/env bash
set -euo pipefail

: "${AZURE_SUBSCRIPTION_ID:?Set the owner-selected Azure subscription.}"
: "${TARGET_RESOURCE_GROUP:?Set the test resource group.}"
: "${NAME_PREFIX:?Set the FinGPT workload prefix.}"
: "${RESTORE_BACKUP_NAME:?Set the exact named PostgreSQL backup.}"
: "${RESTORE_SERVER_NAME:?Set a unique test restore server name.}"
: "${FINGPT_POSTGRES_BACKUP_RETENTION_DAYS:?Set the configured backup retention.}"
: "${AZURE_SUBSCRIPTION_NAME:?Resolve the selected subscription name first.}"

source_server="${NAME_PREFIX}-test-pg"
vnet_name="${NAME_PREFIX}-test-vnet"
vnet_id="$(az network vnet show \
  --subscription "$AZURE_SUBSCRIPTION_ID" \
  --resource-group "$TARGET_RESOURCE_GROUP" \
  --name "$vnet_name" --query id --output tsv)"
subnet_id="$(az network vnet subnet show \
  --subscription "$AZURE_SUBSCRIPTION_ID" \
  --resource-group "$TARGET_RESOURCE_GROUP" \
  --vnet-name "$vnet_name" --name postgresql --query id --output tsv)"
private_dns_zone_id="$(az network private-dns zone show \
  --subscription "$AZURE_SUBSCRIPTION_ID" \
  --resource-group "$TARGET_RESOURCE_GROUP" \
  --name privatelink.postgres.database.azure.com --query id --output tsv)"
source_state="$(az postgres flexible-server show \
  --subscription "$AZURE_SUBSCRIPTION_ID" \
  --resource-group "$TARGET_RESOURCE_GROUP" \
  --name "$source_server" --query state --output tsv)"
earliest_restore_time="$(az postgres flexible-server show \
  --subscription "$AZURE_SUBSCRIPTION_ID" \
  --resource-group "$TARGET_RESOURCE_GROUP" \
  --name "$source_server" --query properties.backup.earliestRestoreDate --output tsv)"
backup_list="$(az postgres flexible-server backup list \
  --subscription "$AZURE_SUBSCRIPTION_ID" \
  --resource-group "$TARGET_RESOURCE_GROUP" \
  --server-name "$source_server" --output json)"
backup_json="$(printf '%s' "$backup_list" \
  | python3 infra/verify_backup.py \
    --name "$RESTORE_BACKUP_NAME" \
    --max-age-hours "$((FINGPT_POSTGRES_BACKUP_RETENTION_DAYS * 24))")"
target_count="$(az postgres flexible-server list \
  --subscription "$AZURE_SUBSCRIPTION_ID" \
  --resource-group "$TARGET_RESOURCE_GROUP" \
  --query "[?name=='$RESTORE_SERVER_NAME'] | length(@)" --output tsv)"

target_exists=()
if [[ "$target_count" != 0 ]]; then
  target_exists=(--target-exists)
fi

python3 infra/validate_restore_plan.py \
  --name-prefix "$NAME_PREFIX" \
  --subscription-name "$AZURE_SUBSCRIPTION_NAME" \
  --source-server-name "$source_server" \
  --source-server-state "$source_state" \
  --earliest-restore-time "$earliest_restore_time" \
  --backup-json "$backup_json" \
  --restore-server-name "$RESTORE_SERVER_NAME" \
  --vnet-id "$vnet_id" \
  --postgres-subnet-id "$subnet_id" \
  --private-dns-zone-id "$private_dns_zone_id" \
  "${target_exists[@]}"
