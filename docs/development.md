# Development guide

## Repository layout

- `backend/` contains the replacement Python API, worker, migrations, and
  backend dependency configuration.
- `web/` contains the React, TypeScript, and Vite application.
- `infra/` contains Bicep definitions. It must never assign roles.
- `docs/` is the maintained product and engineering knowledge base.
- Root `AGENTS.md` applies repository-wide. This file is the source for local
  setup and development commands as the new application becomes executable.

The existing `main.py`, `src/`, root `requirements.txt`, and root Docker setup
are legacy reference material during the rebuild. Do not add new product logic
there. Remove or replace them only when the new application reaches the
corresponding approved milestone.

## Development rules

- Python APIs use type hints and docstrings where behavior is not obvious.
- TypeScript runs with strict type checking. Avoid `any` and unchecked casts.
- Store money in exact decimal form and validate at API boundaries.
- Keep route handlers thin and keep Foundry output untrusted until validated.
- Persist drafts before asking follow-up questions. Link each transaction to its
  source evidence and source conversation item.
- Update this documentation in the same change as behavior, API, configuration,
  or deployment changes.
- Private uploaded evidence uses filesystem storage for local development and
  requires Azure Blob Storage in production. See `deployment.md` for settings.
- CSV imports are previews: map required amount and description columns, review
  row errors, then confirm each valid row independently. Dates are never inferred.
- The transaction screen no longer exposes CSV import. Its list uses the
  owner-scoped paginated `GET /api/transactions` with `from`, `to`, `kind`,
  `q`, and `offset`; its dated summary passes the same search/type filters to
  `GET /api/transactions/reports/totals`. Aggregates exclude deleted records
  even when the list includes them. The CSV import API and past evidence stay
  available for existing records.
- Web chat defaults to read-only Q&A. Use a fake `CHAT_ASSISTANT` in backend API
  tests; no paid Foundry call is needed. Transaction-mode replies update versioned
  missing date/period facts but never perform the separate confirmation step.
- The VI/EN selector persists to browser `localStorage` under `fingpt-language`.
  English UI copy does not change the Vietnamese-first transaction parser.
- Never commit or push unless each action is explicitly requested.

## Local prerequisites

- Python 3.12 or later and `uv` for reproducible backend setup.
- Node.js 22.12 or later, Corepack, and pnpm 11.25.0 for the web app.
- Docker Compose for local PostgreSQL 18.
- Azure credentials are not required for unit-level domain work. Use local
  fakes for Telegram, Service Bus, Blob Storage, and Foundry.

## Commands

Backend development commands from the repository root:

```bash
docker compose -f backend/compose.yaml up -d postgres
cp backend/.env.example backend/.env
# Generate a local key, then replace FIN_GPT_SECRET_KEY in backend/.env.
uv run --project backend python -c 'import secrets; print(secrets.token_urlsafe(48))'
uv sync --project backend --extra dev --frozen
set -a && source backend/.env && set +a
uv run --project backend alembic -c backend/alembic.ini upgrade head
uv run --project backend gunicorn --chdir backend 'fingpt:create_app()'
uv run --project backend ruff check backend/fingpt backend/migrations backend/tests
uv run --project backend ruff format --check backend/fingpt backend/migrations backend/tests
uv run --project backend mypy backend/fingpt backend/migrations backend/tests
uv run --project backend pytest backend/tests
uv run --project backend flask --app 'fingpt:create_app()' bootstrap-owner my-owner-name
```

The liveness endpoint is `/health/live`; readiness is `/health/ready` and
requires a reachable PostgreSQL database. Frontend commands from `web/`:

```bash
cd web
pnpm install --frozen-lockfile
pnpm dev
pnpm lint
pnpm format:check
pnpm typecheck
pnpm build
```

CI uses the hash-pinned `backend/requirements-dev.lock` for Python tooling and
the frozen `web/pnpm-lock.yaml` for JavaScript tooling. The API container
installs its runtime from the hash-pinned `backend/requirements-runtime.lock`.
Regenerate lockfiles deliberately when dependency manifests change, then run
the full local checks and container build.

See [authentication implementation](authentication.md) for owner bootstrap,
password, session, CSRF, and login-throttling policies.

Do not infer that legacy root commands start the rebuilt application.

The Telegram text webhook and queue worker are also part of the backend. Local
webhook tests inject secret resolvers and never call Telegram or Azure. The
worker is started separately with `uv run --project backend python -m fingpt.worker`
after Service Bus settings are available. Local development may use a connection
string; production requires `SERVICE_BUS_NAMESPACE` and managed identity. It uses
the same backend image and a separate process role.
