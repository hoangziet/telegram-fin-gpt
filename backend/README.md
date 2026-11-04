# FinGPT backend

This is the clean replacement backend package. The root `main.py` and `src/`
remain legacy reference code while the new milestones are built.

## Current foundation

- Flask application factory with separate liveness and database readiness
  endpoints.
- Exact decimal money parsing with no implicit rounding.
- Transaction date/time domain type with explicit timezone and day period.
- PostgreSQL connection pooling configured with pre-ping.

## Configuration

Set `DATABASE_URL` to a PostgreSQL connection string and
`FIN_GPT_SECRET_KEY` to a secret value before enabling authenticated sessions.
Never use a development value in a deployed environment.

The backend dependency manifest is `pyproject.toml`, and `uv.lock` pins the
resolved development environment. From the repository root, run
`uv sync --project backend --extra dev --frozen`, then start PostgreSQL with
`docker compose -f backend/compose.yaml up -d postgres`. Copy `.env.example` to
`.env` and replace its development secret. Export those variables before
running the API with `uv run --project backend gunicorn --chdir backend
'fingpt:create_app()'`. Apply schema changes with
`uv run --project backend alembic -c backend/alembic.ini upgrade head` after the
database is available.
The container listens on port 8080.

Database models, Alembic migrations, API use cases, and the worker will be added
in their planned slices.
