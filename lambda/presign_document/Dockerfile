# Lambda image for presign-document (Chapter 4's first Lambda function).
#
# Base = the AWS Lambda python3.12 runtime image — the exact runtime the
# function executes under in production, so this image doubles as the local
# test harness (build context is the repo root; run from there).
#
# Deps install into /var/task/.venv via the shared uv.lock. Lambda's Python
# adds /var/task to sys.path but not the venv, so PYTHONPATH exposes the
# venv's site-packages — our locked boto3 (not the image's driftable copy)
# is what the handler imports.
#
# Build:   docker build -f lambda/presign_document/Dockerfile -t rag-agent-presign:dev .
# Test:    docker run --rm --entrypoint python rag-agent-presign:dev -c "..."

FROM public.ecr.aws/lambda/python:3.12

COPY --from=ghcr.io/astral-sh/uv:0.12.9 /uv /uvx /bin/

WORKDIR /var/task

ENV PYTHONPATH=/var/task/.venv/lib/python3.12/site-packages \
    UV_LINK_MODE=copy

# Phase 1 — dependencies only (cached until manifests change).
COPY pyproject.toml uv.lock README.md ./
# Phase 2 — core package (rag_agent is imported from /var/task directly).
COPY rag_agent ./rag_agent
RUN uv sync --frozen --no-dev --no-install-project

# Phase 3 — this function's handler at the Lambda import root.
COPY lambda/presign_document/handler.py ./handler.py

# Lambda runtime invokes handler.lambda_handler(event, context).
CMD ["handler.lambda_handler"]
