# Lambda image for index-document (Chapter 8's vector-store loader).
#
# Same pattern as the ingest/embed images: AWS Lambda python3.12 runtime
# image, shared uv.lock deps into /var/task/.venv, PYTHONPATH exposes the
# venv. psycopg[binary] (added Chapter 8) provides the driver as a linux
# wheel inside this image; the lock file carries per-platform markers so the
# same uv.lock serves the Windows host and every container.
#
# Production connection config comes from AWS Secrets Manager (SECRET_ARN
# env var, set in Terraform); the function must run inside the RDS VPC.
# Local/dev uses DB_DSN instead — see the handler module docstring.
#
# The handler module is index_handler.py (not handler.py): the host test
# suite imports all Lambda handlers into one process and presign owns the
# bare name. Repo name == import name, so no rename at COPY time.
#
# Build:   docker build -f lambda/index_document/Dockerfile -t rag-agent-index:dev .
# Test:    docker run --rm --entrypoint python rag-agent-index:dev -c "..."

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
COPY lambda/index_document/index_handler.py ./index_handler.py

# Lambda runtime invokes index_handler.lambda_handler(event, context).
CMD ["index_handler.lambda_handler"]
