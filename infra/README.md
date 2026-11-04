# Azure infrastructure

`resource-group.bicep` creates the subscription-scoped Resource Group.
`main.bicep` defines the shared Azure platform. `apps.bicep` defines the
Container Apps migration job and runtime apps. Templates contain no role
assignments; build/lint makes no Azure changes. The platform uses VNet/NAT for
stable outbound IP, limits PostgreSQL firewall access to that IP, and creates no
private endpoints. Blob data is private; other service endpoints use public
networking with managed identity/RBAC authorization.

## Required owner decisions

Before deployment, verify the explicitly selected subscription ID and tenant.
Current dev defaults are Southeast Asia, a `/16` VNet and `/23` Container Apps
subnet, 7-day PostgreSQL backup retention, 30-day evidence retention, and one
low-capacity `gpt-4.1-mini` deployment. Confirm these test settings before
storing real evidence. Supply the PostgreSQL administrator password securely.
Set an alert email only if notifications are wanted.
Static Web Apps is separately placed in East Asia because Southeast Asia is not
a supported Static Web Apps region; its static content is globally distributed.

## Validate without Azure access

```sh
python3 -m unittest discover -s infra -p 'test_*.py'
python3 infra/validate_network.py \
  --vnet 10.40.0.0/16 \
  --aca-subnet 10.40.0.0/23
az bicep build --file infra/main.bicep --stdout >/dev/null
az bicep lint --file infra/main.bicep
az bicep build --file infra/apps.bicep --stdout >/dev/null
az bicep lint --file infra/apps.bicep
```

The CIDRs in the example are illustrative only. Use ranges that do not overlap
the owner-selected Azure network plan.

## Preview and deploy

Create a private parameter file from `main.bicep`'s parameter declarations.
Never put the PostgreSQL administrator password in a checked-in file. Use a
secure secret prompt/environment injection and keep the file outside source
control. First inspect a preview:

```sh
az account show --query name --output tsv
az deployment group what-if \
  --resource-group "$RESOURCE_GROUP" \
  --template-file infra/main.bicep \
  --parameters @"$PARAMETER_FILE"
```

Verify the active subscription ID matches the explicitly selected
`AZURE_SUBSCRIPTION_ID`; do not use the display name as an allow/deny rule.
Review the complete what-if output before running `az deployment group create`
with the same template, resource group, and parameters. The deployment flow
must remain manual and use GitHub OIDC only after the owner configures a
federated identity and grants the documented deployment scope. It must never
create role assignments.

For the GitHub workflow, set repository variables `FINGPT_NAME_PREFIX`,
`FINGPT_OWNER`, `FINGPT_ALERT_EMAIL_ADDRESS`, `FINGPT_AZURE_LOCATION`,
`FINGPT_VNET_CIDR`,
`FINGPT_ACA_SUBNET_CIDR`, `FINGPT_POSTGRES_ADMIN_LOGIN`,
`FINGPT_POSTGRES_BACKUP_RETENTION_DAYS`, and `FINGPT_EVIDENCE_RETENTION_DAYS`.
Set secrets `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, and
`FINGPT_POSTGRES_ADMIN_PASSWORD`, plus `FINGPT_DEPLOYMENT_FINGERPRINT_KEY`.
Generate the fingerprint key with
`python3 -c 'import secrets; print(secrets.token_hex(32))'` and save it only as
a GitHub Actions secret. OIDC values must reference an owner-selected,
approved subscription.

## Runtime deployment

`apps.bicep` uses the same backend image for the API, worker, and one-shot
Alembic job. It uses the API and worker user-assigned identities for Key Vault
references and the pull identity for ACR. The API exposes liveness/readiness
probes; the Static Web App serves the SPA and proxies `/api` to the API. Add each Telegram
secret as an `apiRuntimeSecrets` or `workerRuntimeSecrets` entry with a
Key Vault secret URI, a lowercase Container Apps secret name, and its
environment variable name. Give the identity used by that app access to those
secrets before deployment. Supply an immutable image reference, not `latest`.
The API and worker set `AZURE_CLIENT_ID` to their own user-assigned identity so
the Azure SDK selects the intended identity when the pull identity is also
attached. The worker namespace setting is normalized from the ARM Service Bus
endpoint to the hostname required by the SDK.

Keep `deployRuntimeApps=false` for the first apps-template deployment. Set
`createMigrationJob=true`, review `what-if`, deploy the job, take a named
database backup, then start exactly one job execution manually and verify its
exit code and logs. Only after it succeeds, set `deployRuntimeApps=true`, review
the preview, and deploy API and worker. The database and session secrets must
already exist in Key Vault; roles in `RBAC.md` must be granted manually. The
Container Apps reaches public service endpoints over NAT egress. ACR publishing
requires a principal with the scoped `AcrPush` role.

Use separate private parameter files for the migration and runtime phases.
Review the whole preview before each deployment:

```sh
az account show --query name --output tsv
az deployment group what-if -g "$RESOURCE_GROUP" \
  -f infra/apps.bicep -p @"$MIGRATION_PARAMETER_FILE"
az deployment group create -g "$RESOURCE_GROUP" \
  -f infra/apps.bicep -p @"$MIGRATION_PARAMETER_FILE"
az containerapp job start -g "$RESOURCE_GROUP" \
  -n "$NAME_PREFIX-$ENVIRONMENT-migrate"
az containerapp job execution list -g "$RESOURCE_GROUP" \
  -n "$NAME_PREFIX-$ENVIRONMENT-migrate" -o table
```

Inspect the execution and logs, then prepare a runtime parameter file with
`deployRuntimeApps=true`. Review its full what-if output before applying it.
Before running deployment commands, verify the active subscription ID and tenant
match the reviewed target.

The manual `.github/workflows/apps-deploy.yml` workflow provides the same
phases plus a `smoke-check` phase for the HTTPS liveness, readiness, and web
shell endpoints, and the active worker revision health. It uses OIDC to inspect
the worker through Azure Resource Manager and requires read access to the app.
Supply `api_base_url` without a trailing slash. Configure repository variables
`FINGPT_DATABASE_URL_SECRET_URI`,
`FINGPT_SESSION_KEY_SECRET_URI`, `FINGPT_API_MAX_REPLICAS`,
`FINGPT_WORKER_MAX_REPLICAS`, `FINGPT_API_RUNTIME_SECRETS_JSON`, and
`FINGPT_WORKER_RUNTIME_SECRETS_JSON`. The JSON variables contain arrays of
`name`, `keyVaultUrl`, and `environmentVariable` values; put only Key Vault
secret URIs there, never the secret values. The database URI must refer to the
least-privilege application database user, not the PostgreSQL administrator.
The session-key secret must have at least 32 random bytes. Keep runtime apps'
secret URI entries limited to the environment variables they need.

Choose phase `migration-job` to preview or deploy the one-shot job. Choose
`database-backup` with a unique backup name to preview and request the named
PostgreSQL on-demand backup. Wait for Azure to report its completion. Then use
`start-migration` with that same name; the workflow requires a completed
customer on-demand backup less than 24 hours old both before preview and again
after approval. It verifies the migration image digest, exact Alembic command,
manual trigger, one-replica parallelism, and zero retries both before preview
and after approval. Review the listed executions before approving a start.
After its execution succeeds, choose `runtime-apps` to preview or deploy the
API and worker. Each `apply` run first shows its preview and then waits for the
GitHub `production` environment
approval. Configure OIDC federation for both the repository branch subject used
by preview and the `production` environment subject used by apply. This
workflow has not been run; setting up the OIDC principal and manual role grants
is still an owner task.

The template uses 1 vCPU and 2 GiB per container as conservative starting
values. Validate sizing, scaling, and health behavior in an approved
non-production environment before production. `apiMaxReplicas` and
`workerMaxReplicas` are required inputs. Worker scaling currently uses CPU
utilization; tune against measured queue age and throughput before production.
The infrastructure template also creates email alerts for PostgreSQL CPU above
85%, PostgreSQL storage above 80%, and any dead-lettered Service Bus message.
It also alerts when average active queue messages exceed 100 over ten minutes.
The runtime template adds an API 5xx count alert and a worker zero-replica
alert. Treat these thresholds as initial operating values and validate alert
delivery and tune them in the approved environment. The platform template must
be deployed first so the runtime template can reference its action group.

The manual `.github/workflows/restore-rehearsal.yml` workflow is restricted to
the FinGPT `test` PostgreSQL server. It previews the selected retained
customer on-demand backup, source restore window, unique target name, VNet,
delegated subnet, and private DNS zone. Protected apply rechecks those values
and requests a new server with `--no-wait`. It does not delete the restored
server. After provisioning, it runs the private `${namePrefix}-test-rverify`
Container Apps job with the restored server hostname. The job uses the
application database URL secret and runs `python -m fingpt.restore_verification`
for a read-only connectivity, migration head, table, column, named constraint,
and index check. It starts the real Flask app with the restored DB connection
and calls its liveness/readiness routes. It does not select or log financial
row contents; an optional `FINGPT_SYNTHETIC_CANARY_ID` validates designated canary
record presence in read-only mode. Operators evaluate RPO/RTO metrics using
`python3 infra/measure_recovery.py`, then delete the temporary server only after
review using `bash infra/cleanup_restore_server.sh`. This workflow has not been
run in Azure. Successful workflow summaries retain the recovery point, elapsed
time, backup name, and verifier execution ID without secret connection data.

## Runtime integration still required

- Assign the roles listed in the root `RBAC.md` manually at reviewed scopes.
- Configure `FINGPT_DEPLOYMENT_ENV=production`, `SERVICE_BUS_NAMESPACE`, and
  `SERVICE_BUS_QUEUE_NAME`; the worker now uses managed identity in production.
- Copy `identityPrincipalIds` from the successful deployment output and have the
  owner assign the scoped roles in `RBAC.md` before creating app revisions.
- Configure the Container Apps registry setting to use the image-pull UAMI.
- Configure the database application principal and least-privilege grants.
- Deploy API and worker Container Apps using the ordered sequence above.
- Publish the immutable image through a trusted VNet-connected runner before
  starting the application workflow; GitHub-hosted runners cannot reach private
  ACR to push images.
- Configure private Foundry connectivity only after choosing and evaluating a
  model deployment.
- Verify backup restore, evidence expiry, queue retries/dead letters, health
  probes, alerts, and rollback in the chosen subscription before production.

No Azure resources, credentials, or role assignments have been created by this
repository change.
