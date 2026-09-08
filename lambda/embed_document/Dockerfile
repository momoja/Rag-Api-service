# Lambda image for embed-document (Chapter 7's embedding function).
#
# Same pattern as lambda/ingest_document/Dockerfile: AWS Lambda python3.12
# runtime image, shared uv.lock deps into /var/task/.venv, PYTHONPATH exposes
# the venv. Bedrock Runtime calls need only boto3; pypdf rides along in the
# shared lock but is unused here. No DOCUMENTS_BUCKET env var: the bucket
# name comes from each S3 event record (see the handler module docstring).
#
# The handler module is embed_handler.py (not handler.py) because the host
# test-suite imports all Lambda handlers into one process; presign already
# owns the bare name. Keeping the repo name identical to the import name
# avoids a rename at COPY time.
#
# Build:   docker build -f lambda/embed_document/Dockerfile -t rag-agent-embed:dev .
# Test:    docker run --rm --entrypoint python rag-agent-embed:dev -c "..."

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
COPY lambda/embed_document/embed_handler.py ./embed_handler.py

# Lambda runtime invokes embed_handler.lambda_handler(event, context).
CMD ["embed_handler.lambda_handler"]
