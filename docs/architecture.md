# FinGPT architecture

## Deployment shape

Use a modular Python application with separately scaled API and worker
processes. Keep a React single-page application on the same public origin as the
API where practical. Do not introduce Kubernetes, a vector database, or
microservices without measured need.

```text
Browser ─┐
         ├─ HTTPS ingress / Container Apps API ─ PostgreSQL
Telegram ┘                  │          │
                            │          └─ transactional outbox
                            └─ Service Bus ─ Container Apps worker
                                         ├─ Azure AI Foundry
                                         └─ Blob Storage evidence
```

The API validates ownership and durably records inbound work before returning a
successful webhook response. A worker consumes queued work. Queue delivery may
repeat; database uniqueness and idempotent state transitions prevent duplicate
writes. Use an outbox or equivalent transaction boundary when publishing work.

## Responsibilities

- `web/`: React/TypeScript/Vite user interface, typed API client, accessible
  transaction preview, evidence viewer, dashboard, search, reports, VI/EN UI,
  and one chat surface for questions and transaction proposals.
- `backend/fingpt/api/`: Flask API routes, request validation, session auth, and
  API error responses. Routes do not contain business or SQL logic.
- `backend/fingpt/domain/`: transaction, draft, confirmation, undo, and report
  rules independent of Flask and Azure SDKs.
- `backend/fingpt/services/`: application use cases and orchestration.
- The chat coordinator routes messages to two specialists: the Q&A specialist
  receives only owner-filtered monthly aggregates and bounded conversation
  context; the transaction specialist uses deterministic parsing and creates a
  durable draft. Ambiguous input defaults to Q&A. Neither specialist confirms
  transactions; only the explicit version-checked confirmation route writes.
- Keep routing and specialist interfaces extensible so future capabilities can
  be added without adding separate user-facing chat modes. Model output is never
  interpreted as a database command or confirmation.
- `backend/fingpt/adapters/`: PostgreSQL repositories, Telegram, Service Bus,
  Blob Storage, and Foundry implementations.
- `infra/`: Bicep resources and non-secret configuration. No role assignments.
- `docs/`: requirements, architecture, development, deployment, and operations.

## Data ownership and boundaries

- Every user-owned record has a `user_id`; authorization checks ownership on
  every route, callback, query, evidence download, and background action.
- Keep bot configuration separate from users so one user can later own multiple
  bot identities. Store bot API and webhook verification secret references
  separately; never store their values in browser-readable data.
- Use PostgreSQL migrations with stable identifiers and constraints. Store money
  as fixed-precision decimal values and currency code, never binary floats.
- Store transaction date, exact local time, day period, timezone, and system
  timestamps as distinct fields. Text drafts require an explicit clock time;
  other import types retain their source-specific requirements. A period never
  fabricates a clock time.
- Drafts have status and monotonically increasing version. Confirmation uses
  the expected version and a unique idempotency key.
- Store immutable transaction revisions or audit events for undo and conflict
  detection. Do not erase the evidence of the prior value when correcting it.
- Evidence metadata lives in PostgreSQL; private file bytes live in Blob Storage.
  Download through short-lived authorized application links, not public blobs.
- Local development uses a private filesystem adapter. Production requires the
  Azure Blob adapter and managed identity; application startup refuses local
  file storage when `FINGPT_DEPLOYMENT_ENV=production`.
- Conversation context is scoped to a channel conversation. Web and Telegram
  share finance data but do not implicitly share pending conversation context.

## Security and operations

- Use username/password login with password hashing, secure server-side
  sessions, CSRF protection, login throttling, and a one-time owner bootstrap.
- The owner is bootstrapped once through an interactive Flask CLI command after
  the schema migration. There is no public registration endpoint. Usernames are
  canonical lowercase ASCII identifiers; new passwords are 15–128 characters.
- Hash passwords with Argon2id. Use random 256-bit session bearer tokens in
  `HttpOnly`, `SameSite=Strict` cookies; keep only the SHA-256 verifier in
  PostgreSQL, expire sessions after 12 hours, and revoke them server-side.
- Bind CSRF tokens to a session and require them in `X-CSRF-Token` for every
  authenticated state-changing API request. Authentication fails closed if
  PostgreSQL is unavailable. Never accept a user identity from request data.
- Throttle sign-in attempts in PostgreSQL at ten attempts per client address in
  a 15-minute window, using a keyed digest instead of persisting raw IPs. This
  is shared across API replicas. Trust forwarded client IP headers only after
  the ingress proxy chain is explicitly configured and verified.
- Require a random `FIN_GPT_SECRET_KEY` with at least 32 bytes. Production must
  use HTTPS-only `__Host-` cookies; local HTTP development can opt into
  `AUTH_COOKIE_SECURE=false` with no production credentials.
- Scope every future data query and write to the authenticated user. Database
  composite foreign keys provide a second ownership boundary, not a replacement
  for application authorization.
- Use Azure Managed Identity for service access after the owner assigns the
  roles documented in `RBAC.md`.
- Keep Azure role assignment out of Bicep and deployment workflows.
- Use Application Insights/Azure Monitor without logging tokens, raw financial
  content, receipt contents, or passwords.
- Liveness and readiness are separate. Readiness checks required dependencies;
  health endpoints do not expose secrets or internal configuration.
- Configure backups and verify restore before production cutover.
