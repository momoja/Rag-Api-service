# Lambda image for ingest-document (Chapter 6's ingestion function).
#
# Same pattern as lambda/presign_document/Dockerfile: AWS Lambda python3.12
# runtime image, shared uv.lock deps into /var/task/.venv, PYTHONPATH exposes
# the venv. pypdf (added Chapter 6) is a locked runtime dependency, so it is
# present in this image for PDF extraction.
#
# The handler module is named ingest_handler.py (not handler.py) because the
# host test-suite imports both Lambda handlers into one process and the
# presign handler already owns the bare name. The module name only matters
# per-image, but keeping the repo name identical to the import name avoids a
# rename at COPY time.
#
# Build:   docker build -f lambda/ingest_document/Dockerfile -t rag-agent-ingest:dev .
# Test:    docker run --rm --entrypoint python rag-agent-ingest:dev -c "..."

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
COPY lambda/ingest_document/ingest_handler.py ./ingest_handler.py

# Lambda runtime invokes ingest_handler.lambda_handler(event, context).
CMD ["ingest_handler.lambda_handler"]
