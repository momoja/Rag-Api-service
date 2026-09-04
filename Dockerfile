# Dev/runtime image for rag-agent.
#
# python:3.12 lineage matches the AWS Lambda python3.12 runtime that all
# compute runs on from Chapter 4 on — code behaves identically in this
# container and in production. uv mirrors the host toolchain (see pyproject).
#
# Hermetic by design: dependencies AND source are baked in. No bind mounts —
# a host bind mount would mask this layer with the Windows-built .venv and
# defeat the Lambda-parity guarantee. Rebuild for changes; the dep layer is
# cached until pyproject.toml / uv.lock change.

FROM python:3.12-slim

# uv (pinned to the host's 0.12.x line) — fast, lockfile-driven env management
COPY --from=ghcr.io/astral-sh/uv:0.12.9 /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Phase 1 — dependencies only. Cached until the manifests change.
# README.md is required at build time (hatchling reads it for metadata).
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project

# Phase 2 — project code + tests. Changes here do not invalidate phase 1.
COPY rag_agent ./rag_agent
COPY tests ./tests
RUN uv sync --frozen

# Default command runs the suite; override per-invocation (see compose.yaml).
CMD ["uv", "run", "pytest"]
