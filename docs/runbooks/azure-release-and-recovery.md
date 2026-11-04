# Azure release and recovery runbook

This runbook is an operational procedure, not evidence that a cloud recovery
exercise has passed. Record the exercise date, target, results, and measured RPO
and RTO after rehearsing in an approved non-production subscription.

## Release sequence

1. Select the reviewed commit SHA and build one immutable backend image. Do not
   deploy mutable tags such as `latest`.
2. Review the infrastructure `what-if` output and explicitly approve the
   protected GitHub environment before applying infrastructure changes.
3. Confirm that the expected app revision is healthy and that the release image
   can be pulled from the private registry.
4. Before schema changes, create a named PostgreSQL backup and record its UTC
   completion time. The `database-backup` workflow phase previews the unique
   backup name and requests an on-demand backup. Wait for Azure to list that
   name as `Customer On-Demand` with a completion timestamp. The
   `start-migration` phase requires that exact backup to be less than 24 hours
   old and checks again after approval. Azure Flexible Server restores create a
   new server; they do not overwrite the source. To create a backup manually:

   ```sh
   az postgres flexible-server backup create \
     --resource-group "$RESOURCE_GROUP" \
     --server-name "$SOURCE_SERVER" \
     --name "$BACKUP_NAME"
   ```
5. Deploy `apps.bicep` with `createMigrationJob=true` and
   `deployRuntimeApps=false`, after reviewing its what-if output. Run the
   migration as a one-shot Container Apps Job in the same private network,
   using the same immutable image and migration configuration as the release.
   Its command is:

   ```sh
   alembic -c /app/alembic.ini upgrade head
   ```

   Use one execution with zero retries for schema changes. Inspect the job exit
   status and logs; logs must not contain credentials or financial content.
6. After the migration job succeeds, deploy `apps.bicep` again with
   `deployRuntimeApps=true` and the same image SHA. Preserve the old API
   revision until smoke checks pass. The worker must use managed identity for
   Service Bus and must be ready to consume the current event schema.
7. Run smoke checks below. If they pass, record the image SHA, migration
   revision, operator, and result. If they fail, use the application rollback
   procedure. Do not assume a database downgrade is safe.

Container Apps manual jobs are intended for finite tasks such as migrations and
share their environment's networking and logging capabilities; see [Microsoft
Learn: Container Apps jobs](https://learn.microsoft.com/en-us/azure/container-apps/jobs).

## Post-deployment smoke checks

Set `API_BASE_URL` to the reviewed HTTPS API endpoint. These checks do not
authenticate or read user data:

```sh
curl --fail --silent --show-error "$API_BASE_URL/health/live"
curl --fail --silent --show-error "$API_BASE_URL/health/ready"
```

The liveness route checks the API process. Readiness checks PostgreSQL only; it
does not prove queue, Telegram, Blob Storage, or Foundry connectivity. Test
Service Bus delivery using a synthetic, non-financial event in a test queue and
verify enqueue, receive, completion, retry, and dead-letter behavior. Test Blob
Storage with a synthetic file through authenticated owner-scoped upload and
download, then delete it and verify access is denied. Do not place production
receipt content in smoke tests.

The synthetic cloud smoke CLI `python -m fingpt.cloud_smoke` automates these
non-financial verification checks for Service Bus and Blob Storage with
`--test-service-bus` and `--test-storage`.

The cloud queue and Blob Storage tests have not been executed. The runtime
deployment template exists, but has not been applied in Azure; associated
identities still require manual role grants before these procedures can run.

## Backup and restore rehearsal

PostgreSQL backup retention is a required infrastructure parameter. Select it
from the approved recovery policy; do not infer an RPO or RTO from the backup
setting alone. See [PostgreSQL backup and restore concepts][pg-backup].

1. Use `.github/workflows/restore-rehearsal.yml` for the test server. Choose a
   retained customer on-demand backup. The workflow verifies its completion
   time against the server's restore window and previews the private network
   target before protected apply. For manual operation, identify the source
   server's private DNS zone and delegated subnet.
2. Restore to a unique server name in the approved resource group and region:

   ```sh
   az postgres flexible-server restore \
     --resource-group "$RESOURCE_GROUP" \
     --name "$RESTORE_SERVER" \
     --source-server "$SOURCE_SERVER" \
     --restore-time "$RESTORE_TIME_UTC" \
     --vnet "$VNET_ID" \
     --subnet "$POSTGRES_SUBNET_ID" \
     --private-dns-zone "$POSTGRES_PRIVATE_DNS_ZONE_ID"
   ```

3. Wait for provisioning to complete. The restore workflow runs the private
   `${FINGPT_NAME_PREFIX}-test-rverify` Container Apps job, overriding only the
   hostname in its Key Vault-backed database URL. The command makes a read-only
   connection, checks the deployed Alembic head plus required tables, columns,
   named constraints, and indexes, then starts the real Flask app with the
   restored connection and calls its liveness/readiness routes. It prints
   sanitized status only. An optional `FINGPT_SYNTHETIC_CANARY_ID` can be
   supplied to verify a designated canary record in read-only mode without logging
   content. Do not apply migrations during initial validation.
4. Record actual restore duration and data-loss point. Use
   `python3 infra/measure_recovery.py` to compare them with owner-approved
   RPO/RTO targets. Run `bash infra/cleanup_restore_server.sh` (with preview first,
   then `CONFIRM_CLEANUP=true MODE=apply`) to delete the temporary restore
   server after evidence is recorded and reviewed.

Microsoft documents that Flexible Server restore creates a new server and
accepts a UTC restore time; see [restore with Azure CLI][pg-cli-restore].
No restore has been demonstrated for FinGPT.

## Application rollback

Container Apps revisions preserve immutable app snapshots. Configure the API
revision mode to `multiple` before production so the prior revision stays
available while the new release is checked. Keep the prior revision active
until smoke checks pass. On failure, reactivate the previous API revision and
route traffic back to it; stop the failed worker revision and reactivate the
previous known-good revision. Record the target revision and verify health
after the switch. The script `bash infra/rollback_revisions.sh` automates this
reversal with preview and apply safeguards. Use the [Container Apps revision management guide][aca-revisions]
for CLI syntax that matches the deployed revision mode.

Application rollback does not reverse a database migration. If a release has
made an incompatible schema change, stop writes, assess the migration, and use
the approved restore/forward-fix plan. Do not point production at a restored
server until its data, schema, and recovery impact have been reviewed.

## Monitoring and incident evidence

- Add alerts for API readiness failures, worker restarts, queue age, and failed
  image pulls after application telemetry and workload baselines are available.
- Current alerts cover PostgreSQL storage/CPU, Service Bus backlog/dead
  letters, API 5xx responses, and a worker with zero replicas. Test the action
  group and alert delivery before relying on it. Add availability checks for API
  readiness and failed image pulls after telemetry baselines are available.
- Preserve Azure deployment IDs, revision names, migration revision, queue
  message IDs, and timestamps. Do not log queue bodies, passwords, session
  tokens, receipt images, or raw financial content.
- Record incident timeline and operator decisions without copying financial
  content into tickets or chat.
- Application Insights resources exist in Bicep, but application telemetry,
  alert rules, dashboards, retention policy for logs, and alert delivery are not
  fully configured yet. Infrastructure creates initial email alerts for high
  PostgreSQL CPU/storage and queue dead letters. The recipient, thresholds,
  email verification, and end-to-end alert delivery still need review in Azure.

[aca-revisions]: https://learn.microsoft.com/en-us/azure/container-apps/revisions-manage
[pg-backup]: https://learn.microsoft.com/en-us/azure/postgresql/backup-restore/concepts-backup-restore
[pg-cli-restore]: https://learn.microsoft.com/en-us/cli/azure/postgres/flexible-server#az-postgres-flexible-server-restore
