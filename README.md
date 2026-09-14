# rag-agent

Self-managed RAG (Retrieval-Augmented Generation) system on AWS, built chapter by
chapter. Core pipeline stages — document ingestion, chunking, embeddings, vector
storage, retrieval, and LLM generation — are implemented and tested in this repo,
not outsourced to a managed RAG service. AWS provides the primitives: S3, Lambda,
Bedrock models, and a managed vector store.

Reference and inspiration (architecture study only, not copied):
[aws-samples/samples-for-rag-solutions/advanced-rag-assistant](https://github.com/aws-samples/samples-for-rag-solutions/tree/main/advanced-rag-assistant)

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (Python 3.12 is managed by uv; pinned in
  `pyproject.toml` for Lambda runtime parity)
- Git

## Setup

```powershell
uv sync          # create .venv and install pinned dependencies
```

## Verify

```powershell
uv run pytest    # run the test suite
uv run ruff check .   # lint
uv run ruff format --check .   # formatting
```

Expected: tests pass (218 passed; 21 DB-gated integration tests skip when no database is running — see below), ruff reports no violations.

## Docker (Lambda-parity dev environment)

The project's compute runs on the AWS Lambda `python3.12` runtime from
Chapter 4 on, so the container image uses the same Python lineage. The image
is hermetic — dependencies and source are baked in (no bind mounts), and the
dependency layer is cached until `pyproject.toml` / `uv.lock` change.

Requires Docker Desktop (daemon running).

```powershell
docker compose build              # build rag-agent:dev
docker compose run --rm app       # run the test suite in the container
docker compose run --rm app uv run ruff check .   # lint in the container
docker compose run --rm app uv run python -c "import rag_agent; print(rag_agent.__version__)"
```

`docker compose up -d db` runs the pgvector store the DB-gated integration
tests use; the `app` service is a one-shot test runner, not a server.

## Lambda (container images)

Lambda functions are container images built from
`public.ecr.aws/lambda/python:3.12` — the exact production runtime.
Build + local-invoke example (Chapter 4's `presign-document`):

```powershell
docker build -f lambda/presign_document/Dockerfile -t rag-agent-presign:dev .
docker run --rm --entrypoint python -e DOCUMENTS_BUCKET=rag-agent-dev-documents-000000000000 rag-agent-presign:dev -c "from handler import lambda_handler; print(lambda_handler({'key':'docs/a.pdf'}, {}))"
```

## Progress

| Chapter | Topic | Status |
|---|---|---|
| 1 | Project foundation | Done |
| 2 | Terraform / IaC | Done* |
| 3 | Docker / local development | Done |
| 4 | AWS Lambda + Python | Done |
| 5 | API layer | Done |
| 6 | Document ingestion | Done |
| 7 | Embeddings | Done |
| 8 | Vector storage | Done |
| 9 | Retrieval | Done |
| 10 | LLM / generation | Done |
| 11 | Complete RAG pipeline | Done |
| 12 | Production improvements | Planned |

\*Infrastructure for chapters 2-11 is defined and `terraform plan`-clean but
**not applied** — no AWS resources exist until you approve `terraform apply`.

## Complete pipeline (local, no AWS)

With no AWS account involved, the whole chain is verifiable today:

```powershell
docker compose up -d db --wait   # pgvector (localhost:5432)
uv run pytest                    # 218 passed (21 DB-gated)
```

`tests/test_pipeline_integration.py` (Chapter 11) runs the real ingest ->
embed -> index -> search/answer handlers over in-memory S3, a fake Bedrock,
and the live pgvector store: document bytes in, cited answer out.

## Decisions

Architectural decisions are recorded in [docs/decisions](docs/decisions/).
