FROM node:24-slim AS web-builder

ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0

WORKDIR /web

RUN corepack enable \
    && corepack prepare pnpm@11.25.0 --activate

COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY web/ ./
RUN pnpm build

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY backend/requirements-runtime.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements-runtime.lock \
    && useradd --create-home --uid 10001 appuser

COPY backend/fingpt ./fingpt
COPY backend/alembic.ini ./alembic.ini
COPY backend/migrations ./migrations
COPY --from=web-builder /web/dist ./web-dist

ENV FINGPT_WEB_DIRECTORY=/app/web-dist

USER 10001:10001

EXPOSE 8080

CMD ["gunicorn", "--bind=0.0.0.0:8080", "--workers=2", "--threads=4", "fingpt:create_app()"]
