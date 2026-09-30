# syntax=docker/dockerfile:1.7
# GORGONA API + reusable customer export. The same image serves the API (default CMD)
# and runs the manual migrate/bootstrap jobs (`gba-db migrate|bootstrap`).
# Deterministic: digest-pinned bases, lockfiles (npm ci, uv sync --locked).
# Runtime: non-root, no build tools, no secrets. Configuration comes from the
# environment at run time.

ARG NODE_IMAGE=node:24-bookworm-slim@sha256:0e0ff40c39bc087845bfb27465a0df4ea419520094bc35842ff83dd8cbe6f9b6
ARG PYTHON_IMAGE=python:3.14-slim-bookworm@sha256:82bc3c539b8813ada9d68c63b40158fa002f7f33de9bf3312a3dfdc0620dff56
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.3@sha256:2d890623d310b57771ce840f0da5eed5fc6d657da05ffaa45d82797b53fa3abc

FROM ${UV_IMAGE} AS uv

FROM ${NODE_IMAGE} AS web
WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
COPY api/pyproject.toml api/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
# pyproject.toml declares readme = "../README.md" (relative to /app).
COPY README.md /README.md
COPY api/src ./src
RUN uv sync --locked --no-dev --no-editable

FROM ${PYTHON_IMAGE} AS runtime
RUN groupadd --system --gid 10001 gba \
 && useradd --system --uid 10001 --gid gba --no-create-home --shell /usr/sbin/nologin gba
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY --from=web /src/web/out /app/web
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GBA_HOST=0.0.0.0 \
    GBA_PORT=8000 \
    GBA_CUSTOMER_WEB_DIR=/app/web
USER 10001:10001
EXPOSE 8000
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2).status == 200 else 1)"]
CMD ["python", "-m", "gorgona_booking"]
