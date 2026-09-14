# Lambda image for retrieve-document (Chapter 9's retrieval endpoint).
#
# Same pattern as the index-document image: AWS Lambda python3.12 runtime,
# shared uv.lock deps into /var/task/.venv, PYTHONPATH exposes the venv.
# Needs boto3 (Bedrock query embedding + Secrets Manager) and psycopg
# (rag_chunks search). pypdf rides along in the lock unused.
#
# Database connection via SECRET_ARN (prod, VPC) or DB_DSN (dev) — see the
# handler docstring. Bedrock is reached through the bedrock-runtime VPC
# endpoint (vpc_endpoints.tf); without it a VPC-attached function has no
# route to the public AWS API.
#
# The handler module is retrieve_handler.py (not handler.py): the host test
# suite imports all Lambda handlers into one process and presign owns the
# bare name. Repo name == import name, so no rename at COPY time.
#
# Build:   docker build -f lambda/retrieve_document/Dockerfile -t rag-agent-retrieve:dev .
# Test:    docker run --rm --entrypoint python rag-agent-retrieve:dev -c "..."

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
COPY lambda/retrieve_document/retrieve_handler.py ./retrieve_handler.py

# Lambda runtime invokes retrieve_handler.lambda_handler(event, context).
CMD ["retrieve_handler.lambda_handler"]
