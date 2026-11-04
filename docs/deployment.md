# Deployment and operations

## Intended Azure resources

- Azure Container Apps environment for separate API and worker applications.
- Azure Database for PostgreSQL Flexible Server with a public firewall rule
  limited to the Container Apps NAT Gateway's static IP.
- Azure Service Bus queue with bounded retry and dead-letter handling.
- Blob Storage for receipts and import evidence, with anonymous access disabled
  and a 30-day lifecycle deletion policy.
- One Azure AI Foundry `gpt-4.1-mini` deployment for chat and receipt extraction.
- Standard Static Web App, with `/api` proxied to the API Container App.
- Azure Container Registry, Key Vault, Log Analytics, and Application Insights.
- Bicep provisions infrastructure resources only. It must not contain
  `Microsoft.Authorization/roleAssignments` resources.

The platform template creates a VNet and NAT Gateway for stable Container Apps
egress, PostgreSQL with firewall access restricted to that NAT IP, private Blob
container, Standard Service Bus, Basic ACR, Key Vault, one Foundry model
deployment, Standard Static Web App in East Asia, Container Apps in Southeast
Asia, monitoring, and separate
API/worker/image-pull identities. No private endpoints are created. The owner
assigns runtime roles from `RBAC.md`. `apps.bicep` defines the migration job,
API app, and worker app. See the
[infrastructure README](../infra/README.md) for required parameters, manual
identity grants, private image publishing, and ordered deployment commands.

## Delivery flow

- GitHub Actions CI validates backend and frontend changes and builds the
  immutable API/worker image; the frontend is deployed separately to Static Web
  Apps.
- CI does not have production secrets and does not call paid Foundry deployments
  on every pull request.
- Azure login uses OIDC after a human configures the federated credential and
  grants the deployment principal the required scope in `RBAC.md`.
- Deployment starts manually. Production requires an explicit environment
  approval. Run the migration Container Apps Job first, then deploy API and
  worker only after inspecting a successful job execution.
- Smoke checks run after deployment. Roll back application revisions using a
  previously built artifact; database rollback is not assumed safe.

## Provisioning guardrail

Select and verify the target subscription by its explicit subscription ID and
tenant before provisioning. Do not use a display-name substring as a safety
policy. Keep role assignments out of infrastructure templates; apply only the
owner-approved assignments and scopes documented in the root `RBAC.md`.

## Current dev test deployment

- Static Web App: `https://ambitious-beach-0f2a4c700.8.azurestaticapps.net`
- API and PostgreSQL: Southeast Asia; Static Web App: East Asia.
- The `owner` login is bootstrapped. Its generated password is held in Key Vault
  as `mvp-owner-password`; retrieve it as an authorized member of `QAI-Demo`:
  ```sh
  az keyvault secret show --subscription 016ae539-9a27-43d3-8213-f5afc933c9f5 \
    --vault-name wmbg-dev-kv --name mvp-owner-password --query value --output tsv
  ```
- The `QAI-Demo` group has `Key Vault Secrets Officer` scoped to `wmbg-dev-kv`.
  This permits reading, setting, and deleting secrets; reduce it to
  `Key Vault Secrets User` if write access is no longer needed.
- The MVP smoke test passed for Static Web App `/api` health/readiness, owner
  login/session, and authenticated model-backed read-only Q&A. Text transaction
  intake remains deterministic and draft-only; the model cannot create or
  confirm transactions. No Telegram bot credentials have been configured.

## Operational decisions still required

- Import file and conversation retention/deletion periods.
- Backup retention, recovery point objective, and recovery time objective.
- Expected request volume and target latency/concurrency.
- Production deployment approval and maintenance window.

## Manual infrastructure workflow

`.github/workflows/infra-deploy.yml` is manually dispatched. Configure GitHub
OIDC secrets `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, and
`AZURE_SUBSCRIPTION_ID`, and secret `FINGPT_POSTGRES_ADMIN_PASSWORD`. Configure
secret `FINGPT_DEPLOYMENT_FINGERPRINT_KEY` with 32 random bytes encoded as 64
lowercase hexadecimal characters. Keep the same value across preview and
apply jobs in both workflows.
repository variables `FINGPT_NAME_PREFIX`, `FINGPT_OWNER`,
`FINGPT_ALERT_EMAIL_ADDRESS`,
`FINGPT_AZURE_LOCATION`, `FINGPT_VNET_CIDR`, `FINGPT_ACA_SUBNET_CIDR`,
`FINGPT_POSTGRES_ADMIN_LOGIN`, `FINGPT_POSTGRES_BACKUP_RETENTION_DAYS`, and
`FINGPT_EVIDENCE_RETENTION_DAYS`. Verify the explicitly configured subscription
ID and tenant before deployment. Create the target resource group with
`infra/resource-group.bicep` after reviewing its name and region.

Run with mode `preview` to inspect the complete Azure what-if output. Mode
`apply` first runs the same preview, then pauses at the GitHub `production`
environment. Configure required reviewers and deployment branch restrictions
on that environment before enabling use. Approve only after reviewing the
preview. Both infrastructure and application workflows fingerprint the full
deployment input set, including secret inputs, and refuse to apply if any value
changes after preview. The apply job rechecks the subscription and does not
assign roles.
This workflow has not been run against Azure; no Azure resources have been
created by it.

`.github/workflows/apps-deploy.yml` handles `apps.bicep` as separate manual
phases: `migration-job`, `database-backup`, `start-migration`, and
`runtime-apps`. The backup phase previews and requests a uniquely named
on-demand PostgreSQL backup. The start phase requires Azure to report that
exact backup as a completed customer on-demand backup less than 24 hours old;
it checks again after approval immediately before starting migration. Its
`smoke-check` phase checks HTTPS liveness, database readiness, and the web shell
and polls the worker's active revision until Azure reports it healthy and
running. It requires the same OIDC sign-in and read access to Container Apps.
In `apply` mode,
the workflow records the phase preview first and waits for the GitHub
`production` environment approval before applying it. The start phase prints
the job's image, command, retry policy, trigger settings, and recent executions.
It verifies those settings again after approval before starting it. Review the
matching workflow run before approving. The workflow has not been run against
Azure.

Configure repository variables `FINGPT_DATABASE_URL_SECRET_URI`,
`FINGPT_SESSION_KEY_SECRET_URI`, `FINGPT_API_MAX_REPLICAS`,
`FINGPT_WORKER_MAX_REPLICAS`, `FINGPT_API_RUNTIME_SECRETS_JSON`, and
`FINGPT_WORKER_RUNTIME_SECRETS_JSON`. Secret JSON entries contain only Key
Vault URIs, never secret values. Use a least-privilege application database
credential. Configure the deployment principal's OIDC federation for both the
branch subject used by preview and the `production` environment subject used
by apply. A trusted VNet-connected runner is still required to publish the
image into private ACR.

`.github/workflows/restore-rehearsal.yml` previews and restores only the
FinGPT `test` server to a new name prefixed with `FINGPT_NAME_PREFIX` and
`-test-restore-`. It validates a named customer backup against the server's
earliest restore point and requires the target name to be unused. Apply waits
for the protected GitHub `production` environment, repeats the checks, and
starts the restore without deleting its result. After provisioning, it runs the
private `${FINGPT_NAME_PREFIX}-test-rverify` Container Apps job, which checks
connectivity, Alembic head, tables, columns, named constraints, and indexes
without reading financial rows. It also starts the Flask app with the restored
database connection and calls its real liveness/readiness routes inside the
job. The job uses the application secrets and overrides only the DB host with
the restored server FQDN. An optional `FINGPT_SYNTHETIC_CANARY_ID` validates
synthetic canary record presence in read-only mode without logging contents.
Record RPO/RTO evidence using `python3 infra/measure_recovery.py` before deleting
the temporary server with `bash infra/cleanup_restore_server.sh`. A successful workflow run
summary retains the backup name, recovery point, request/verification times,
elapsed duration, and verifier execution ID without connection secrets or
financial rows.

## Telegram worker configuration

Run the same backend image with `python -m fingpt.worker` for the worker process.
It requires `DATABASE_URL`, `SERVICE_BUS_NAMESPACE`, and
`SERVICE_BUS_QUEUE_NAME` in production. Production uses managed identity and
rejects Service Bus connection strings. Local development may use
`SERVICE_BUS_CONNECTION_STRING` with local authentication. A bot's
`secret_name` and `webhook_secret_name` fields refer to
separate runtime-injected environment secrets: the Telegram bot API token and
the webhook verification secret. A reference such as `telegram/my-bot-token`
resolves from `FINGPT_SECRET_TELEGRAM__MY_BOT_TOKEN`; slashes become double
underscores and hyphens become single underscores. Do not reuse the bot token
as the webhook secret. Local setup creates no Azure resources or secrets.

The webhook accepts only private one-to-one Telegram chats to prevent private
financial previews from appearing in groups. Configure Telegram's webhook URL
and secret token only after the deployment endpoint and secret references are
ready.

## Private evidence storage

Local development defaults to a private filesystem directory configured by
`FINGPT_EVIDENCE_DIRECTORY` (default `.local/evidence`). Keep this directory
outside source control and restrict access to the application identity. The
API exposes files only through authenticated, owner-scoped evidence endpoints.
Responses disable caching and content sniffing.

Production startup requires `FINGPT_DEPLOYMENT_ENV=production` and
`FINGPT_EVIDENCE_STORAGE_MODE=azure`; it fails closed if filesystem storage is
selected. Configure `FINGPT_EVIDENCE_BLOB_ACCOUNT_URL` and
`FINGPT_EVIDENCE_BLOB_CONTAINER`, and grant the runtime identity the storage
data role listed in `RBAC.md`. The container must be private. The adapter uses
HTTPS and managed identity; it does not create Azure resources or assign roles.

`POST /api/imports/csv` accepts a CSV file, explicit column mapping, duplicate
consent, and a UUID idempotency key. It stores the original file privately and
returns row-level errors and a draft preview. Confirm each valid item
independently; rows with errors cannot be confirmed until corrected. The file
size is limited to 5 MiB and parsing to 10,000 data rows. Decide and configure
retention/deletion periods before production stores financial evidence.

Receipt extraction is disabled until both `FINGPT_FOUNDRY_OPENAI_BASE_URL`
(the HTTPS base ending in `/openai/v1`) and `FINGPT_FOUNDRY_MODEL` (deployment
name) are configured. The API uses its managed identity for inference; this is
service-to-service authentication and does not add Entra sign-in for FinGPT
users. The model receives the image over HTTPS and must return strict structured
output. The backend validates all fields, keeps one receipt-total proposal, and
never creates a transaction without the user's confirmation. Add a Vietnamese
receipt evaluation set and choose a model deployment before enabling this in
production. Grant the API identity the Foundry role listed in `RBAC.md`.

Read-only web Q&A uses the same Foundry base URL and model deployment. Without
both settings, `POST /api/chat/ask` returns `503` rather than a simulated answer.
The API sends only monthly owner-scoped aggregates and bounded client-supplied
conversation turns; these turns are not retained by the backend. Verify the
deployment's Responses API compatibility and managed-identity access before
enabling real Q&A. No browser credential or model key is required.

For operator recovery, run:

```sh
flask --app fingpt.app outbox list --status failed
flask --app fingpt.app outbox retry <event-id>
flask --app fingpt.app queue dead-letters --limit 30
flask --app fingpt.app queue replay-next
```

The first two commands inspect failed database outbox metadata and begin a fresh
bounded publication attempt window. Dead-letter inspection omits message bodies.
Redrive shows the next DLQ event and requires explicit confirmation. It is
at-least-once, retains the stable message ID, and requires idempotent consumers.
Use the same image and runtime identity as the worker; queue commands require
`SERVICE_BUS_NAMESPACE` and `SERVICE_BUS_QUEUE_NAME` in production. Local
operations may use `SERVICE_BUS_CONNECTION_STRING`.
