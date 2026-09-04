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

`docker compose up` has no long-running process yet; it becomes the workflow
once Chapter 4+ services (Lambda handlers, later the vector store) exist.

## Progress

| Chapter | Topic | Status |
|---|---|---|
| 1 | Project foundation | Done |
| 2 | Terraform / IaC (S3 + IAM) | Planned |
| 3 | Docker / local development | Planned |
| 4 | AWS Lambda + Python | Planned |
| 5 | API layer | Planned |
| 6 | Document ingestion | Planned |
| 7 | Embeddings | Planned |
| 8 | Vector storage | Planned |
| 9 | Retrieval | Planned |
| 10 | LLM / generation | Planned |
| 11 | Complete RAG pipeline | Planned |
| 12 | Production improvements | Planned |

## Decisions

Architectural decisions are recorded in [docs/decisions](docs/decisions/).
