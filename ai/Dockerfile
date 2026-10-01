# syntax=docker/dockerfile:1.7
# GORGONA AI learning-plane workers (ADR-0013). One image for every AI Container Apps
# Job: `gba-ai tick | pump-evidence | run | health | bootstrap | migrate`.
# Build context: the ai/ directory. Deterministic: digest-pinned bases (same as the
# platform image), uv.lock. Runtime: non-root, no build tools, no secrets.

ARG PYTHON_IMAGE=python:3.14-slim-bookworm@sha256:82bc3c539b8813ada9d68c63b40158fa002f7f33de9bf3312a3dfdc0620dff56
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.3@sha256:2d890623d310b57771ce840f0da5eed5fc6d657da05ffaa45d82797b53fa3abc

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM ${PYTHON_IMAGE} AS runtime
RUN groupadd --system --gid 10001 gai \
 && useradd --system --uid 10001 --gid gai --no-create-home --shell /usr/sbin/nologin gai
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
USER 10001:10001
STOPSIGNAL SIGTERM
ENTRYPOINT ["gba-ai"]
CMD ["health"]
