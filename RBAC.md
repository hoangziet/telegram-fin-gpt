# Azure RBAC assignments for owner

FinGPT infrastructure templates and deployment code must not create role
assignments. The owner assigns the roles below after explicitly selecting and
verifying the Azure subscription ID and resource group. Subscription display
names are labels, not deployment authorization; do not block or approve a target
based on its name alone.

The Bicep deployment outputs `identityPrincipalIds` for the API, worker, and
image-pull identities. Copy the reviewed principal IDs and resource scopes into
the owner-managed assignment record after deployment. Prefer the narrowest
resource scope that supports the operation.

## Runtime identities

- **API UAMI** (`identityPrincipalIds.api`)
  - `Key Vault Secrets User` on the specific Key Vault for API secrets.
  - `Storage Blob Data Contributor` on the evidence container.
  - `Cognitive Services OpenAI User` on Foundry for chat and receipt extraction.
- **Worker UAMI** (`identityPrincipalIds.worker`)
  - `Key Vault Secrets User` on the specific Key Vault for Telegram secrets.
  - `Azure Service Bus Data Sender` on the specific queue for outbox events.
  - `Azure Service Bus Data Receiver` on the specific queue for background work.
- **Image pull UAMI** (`identityPrincipalIds.imagePull`)
  - `AcrPull` on the specific registry.

## Deployment identities

- **GitHub Actions deployment principal**
  - `Contributor` on the application resource group.
  - This includes the read access required by smoke-check to inspect the worker
    revision. It can also deploy the reviewed infrastructure and app templates.
- **Image publishing principal** on a trusted, VNet-connected runner
  - `AcrPush` on the specific registry for immutable app images.

## Dev operator access

- **QAI-Demo security group** has `Key Vault Secrets Officer` on the dev Key
  Vault only, for MVP secret retrieval and rotation. This role can read, set,
  and delete all secrets in that vault; downgrade to `Key Vault Secrets User`
  if operators only need to retrieve the test login secret.

The infrastructure template creates the Azure Monitor action group and metric
alerts. They require no extra application identity permissions.

Notes:

- Do not grant Owner, User Access Administrator, or Role Based Access Control
  Administrator to the application or deployment identity.
- A human with appropriate access must configure GitHub OIDC federation and
  assign the deployment role. The application deployment identity must not
  assign roles to runtime identities.
- Use the API and worker identities for their respective Container Apps and the
  image-pull identity for both registry configurations. Do not enable ACR admin
  credentials as a shortcut.
- The registry uses a public service endpoint; image pulls and pushes still
  require scoped `AcrPull` and `AcrPush` assignments.
- Container Apps uses VNet/NAT only for stable outbound IP. PostgreSQL's public
  firewall admits that single IP. No private endpoints are created. Storage,
  Key Vault, Service Bus, and Foundry rely on managed identity and RBAC.
- Database authentication should use managed identity where supported. If the
  selected PostgreSQL configuration requires database-level grants, document
  those separately; they are not Azure RBAC assignments.
