# Project Status — 2026-09-07

Handoff record so any session (or clone) can resume without prior conversation
context. Pair with the git log and `docs/decisions/`.

## Current state

- **Chapter 1 — Foundation: DONE** (`684fc89`). uv/Python 3.12 (`rag-agent`),
  pytest+ruff, hatchling, ADR 0001.
- **Chapter 2 — Terraform: DONE, NOT APPLIED** (`bb8b87c`). `infra/dev/`
  defines documents bucket + (ch4) Lambda resources. `terraform plan`:
  **9 to add, 0 to change/destroy**. No AWS resources exist yet.
- **Chapter 3 — Docker: DONE** (`38828bd`). Lambda-parity dev image
  (`rag-agent:dev`, python:3.12-slim + uv), compose `app` service.
- **Chapter 4 — First Lambda: DONE** (`a829828`). `presign-document`
  container Lambda (public.ecr.aws/lambda/python:3.12): `rag_agent/storage.py`
  testable core (presign upload URL, SigV4 forced), thin handler, ECR+IAM+log
  group+function in `infra/dev/lambda.tf`. Host: 22 pytest green; container
  invocation verified 200/400/400/400 with SigV4 URL.
- **Chapter 5 — API layer: DONE** (`67fc190`). HTTP API
  (`aws_apigatewayv2`, `infra/dev/api_gateway.tf`) with one
  route `POST /documents/upload-url` -> presign Lambda (proxy integration,
  payload format v2, `$default` stage + `auto_deploy`, dev CORS allow-all,
  `aws_lambda_permission` for API GW). Handler now parses v2 proxy events
  (JSON body incl. base64; 400/500 envelope unchanged); core untouched.
  New: `tests/test_presign_handler.py` (15 tests, offline via patched presign),
  `tests/conftest.py` (handler import path + env), `outputs.api_invoke_url`,
  dev image copies `lambda/`. Verify: 37 pytest green (host + dev image);
  container invocation with v2 events 200 (SigV4) + 400/400/400; terraform
  plan 14 to add (5 API GW resources). Not applied.
- **Chapter 6 — Document ingestion: DONE** (`65a825c`). uploads/ key policy
  enforced in `rag_agent/storage.py`
  (presign now mints `uploads/*` keys only — ch5 contract extension);
  new `ingest-document` Lambda (`infra/dev/ingest_lambda.tf`,
  `lambda/ingest_document/`) triggered by S3 bucket notification
  (`s3:ObjectCreated:*`, prefix-filtered `uploads/` — staging writes land
  outside the filter, no self-trigger). Core `rag_agent/ingest.py`:
  extract (pypdf PDF + UTF-8 .txt/.md) -> clean (NFKC, newline + control
  normalization, blank-line collapse; markdown structure preserved) ->
  chunk (char-window 1200/overlap 200, whitespace-boundary aware with
  hard-cut for unbroken tokens) -> stage
  `processed/<document_id>/{chunks.jsonl, metadata.json}`; document_id =
  sha256(bucket/key/version) so reprocessing is idempotent and a new
  version stages a new doc. S3 bucket CORS added (decision 10). Content
  errors (unsupported/corrupt/no text) skip without retry; transient
  failures re-raise for S3 batch retry. Verify: 94 pytest (host + dev
  image), ruff clean, terraform plan 22 to add (8 new), ingest image
  builds and handler imports (pypdf 5.9.0 baked). Not applied.
- **Chapter 7 — Embeddings: DONE** (`afcbbcc`). Why embeddings: retrieval
  (ch9) must rank chunks by semantic
  similarity, not keyword overlap; embedding at ingest time makes a query a
  vector lookup away. New `embed-document` Lambda (`infra/dev/
  embed_lambda.tf`, `lambda/embed_document/`) — a second target on the
  bucket notification (`processed/*.jsonl`: chunks.jsonl only, never
  metadata.json) — reads each chunks file and embeds every chunk via
  Bedrock Titan (`rag_agent/embed.py`: `amazon.titan-embed-text-v2:0`,
  1024 dims, normalize=True — the decision-5 contract), staging
  `embedded/<document_id>/embeddings.jsonl` (index/text/vector/token count/
  char offsets; text rides along so ch9 returns context without a second
  read). Retry: 3 attempts, exponential backoff, transient codes only
  (throttle, model timeout/error, service/internal unavailability); model
  input rejections skip the doc, misconfig (AccessDenied) fails loud.
  Path-derived keys -> idempotent; single PutObject after all chunks embed
  -> no partial writes. IAM scoped to the foundation-model ARN (no account
  segment). Verify: 120 pytest (host + dev image), ruff clean, terraform
  plan 28 to add (6 new), embed image builds + handler imports. Not
  applied.
- **Chapter 8 — Vector storage: DONE** (working tree, uncommitted — commit
  on move-on). Store: managed Postgres on RDS (`infra/dev/vector_store.tf`,
  t4g.micro, PG 16.4, private in the default VPC) running the pgvector
  extension; RDS over Aurora/OpenSearch for cost + local parity. Core
  `rag_agent/vector.py`: `rag_chunks` table (document_id, chunk_index,
  text, `vector(1024)`, char offsets, created_at; UNIQUE(document_id,
  chunk_index)), HNSW index (`vector_cosine_ops` — incremental inserts,
  no rebuild), cosine-distance search (`<=>`) with optional document_id
  filter, `replace_document` delete-then-insert (idempotent re-index).
  New `index-document` Lambda (`lambda/index_document/`) — third
  notification target (`embedded/*.jsonl`) — VPC-attached, reads the RDS
  password from Secrets Manager at startup (never env); compose gained a
  `pgvector/pgvector:pg16` db service mirroring RDS (same SQL/driver both
  sides). Testing norm change: `tests/test_vector_integration.py` (11
  tests) runs against the live compose DB and auto-skips when no DB
  answers — plain host suite stays green offline; verify step starts the
  db first. Verify: 156 pytest (host, incl. live-DB integration), ruff
  clean, terraform plan 42 to add (14 new). Not applied.
- **Next: Chapter 9 — Retrieval** (query embedding -> vector search ->
  top-K -> context; tests retrieval independently, ch10 wires the LLM).

## Verified environment facts (2026-09-03)

- OS: Windows 11; git 2.53, uv 0.12.9, Python 3.14 system (3.12.9 in .venv),
  Terraform 1.16.0, AWS CLI 2.36.30, Docker 29.4.1 (Desktop).
- AWS creds valid: account `058264314263`, IAM user `terraform-myapp`.
- Region decision: **us-east-1** (CLI default was us-west-1 — do not use).

## Decisions binding later chapters

1. **Self-managed RAG core on AWS** (ADR 0001): we write ingestion → chunking →
   embeddings → retrieval → generation; AWS supplies S3/Lambda/Bedrock/vector
   store. Managed Bedrock KB explicitly rejected.
2. Region: us-east-1 everywhere. 3. Terraform state: local; when first `apply`
   is approved: bootstrap S3 state bucket + DynamoDB lock, migrate backend,
   then apply. Apply only on explicit user request.
4. IAM added ch4 with Lambda (least privilege, scoped logs + s3:PutObject).
5. Vector store choice deferred to ch8; embedding dimension contract
   (Titan v2 = 1024) drives index mapping.
6. Package `rag_agent` (not `app`). 7. boto3 pinned >=1.35,<2 in pyproject.
8. Lambda deploys = container images (Windows-host friendly); presign handler
   forces SigV4 (SigV2 refused on new buckets — regression test added).
10. API layer = HTTP API (apigatewayv2), payload v2, `$default` stage +
    auto_deploy; one route POST /documents/upload-url with JSON body
    `{"key", "expires_in"}`. No auth yet (route only mints presigned URLs;
    revisit when a route exposes data). CORS dev allow-all, injected by API
    GW so the handler stays header-free; S3 bucket CORS added ch6 (dev
    allow-all, ETag exposed — see s3.tf).
11. Ingest layout (ch6): accepted uploads live under `uploads/` (presign
    enforces, notification filter matches); the pipeline stages
    `processed/<document_id>/` in the SAME bucket, outside the filter so it
    can never retrigger itself. `document_id` = sha256(bucket, key,
    version_id) truncated to 32 hex.
12. Chunking contract (ch6): deterministic char-window, size 1200, overlap
    200, broken at whitespace when >= 60% of the window survives (else hard
    cut for unbroken tokens). Output is `chunks.jsonl` (index/text/start/
    end char offsets into cleaned text) + doc `metadata.json`; Chapter 7
    embeddings read these. 1024-dim embedding contract unchanged (decision
    5).
13. Format scope (ch6): PDF (pypdf) + UTF-8 `.txt`/`.md` only. Unsupported,
    corrupt, or textless docs raise ValueError — handler logs and skips so
    S3 never retries them forever; non-ValueError failures re-raise so the
    notification retries the batch (idempotent via decision 11).
    `CompleteMultipartUpload` counts as an object creation.
14. Embeddings (ch7): separate `embed-document` Lambda, triggered by the
    second bucket-notification target (`processed/` + `.jsonl` suffix, so
    exactly chunks.jsonl). Titan V2 config fixed in `rag_agent/embed.py`:
    model `amazon.titan-embed-text-v2:0`, 1024 dims, normalize=True.
    Vectors staged `embedded/<document_id>/embeddings.jsonl` with chunk
    text (ch9 context) + char offsets + token count. Retry = 3 attempts,
    exponential backoff, transient codes only; ValidationException ->
    ValueError -> skip; AccessDenied/misconfig intentionally NOT skipped
    (fails loud until fixed). Bedrock IAM scoped to the model ARN.
15. Vector store (ch8): Postgres 16 + pgvector on RDS (t4g.micro, default
    VPC, not public). Schema owned by code (`rag_agent.vector.
    ensure_schema`): `rag_chunks` with `vector(1024)` + HNSW
    (`vector_cosine_ops`); search = cosine distance ascending, metadata as
    real columns with WHERE filters. Credentials in Secrets Manager, read
    by the index Lambda at startup (SECRET_ARN); the Lambda runs in-VPC.
    Local parity: compose `db` service = pgvector image; integration tests
    gated on a reachable DB (skip when offline), so the default host
    suite stays hermetic.

## Verify commands

```powershell
uv run pytest                      # 156 passed (integration skipped without DB)
uv run ruff check . && uv run ruff format --check .
terraform -chdir=infra/dev validate
terraform -chdir=infra/dev plan -var-file=dev.tfvars   # 42 to add
docker compose up -d db            # pgvector (localhost:5432)
uv run pytest                      # 156 passed incl. live-DB integration
docker compose build && docker compose run --rm app    # dev image tests (156)
docker run --rm -e DOCUMENTS_BUCKET=<bucket> -v "$env:USERPROFILE\.aws:/root/.aws:ro" `
  --entrypoint python rag-agent-presign:dev -c "<v2-event script>"  # 200/400/400/400
```

## Chapter 9 preview

Retrieval, built independently: user question -> embed the query with the
same Titan model -> vector search (rag_chunks, cosine, top-K) -> ranked
chunks with text for context. Exercise the loop against the compose DB
(and, at apply time, the real S3 -> pipeline -> RDS chain) before any LLM
is wired; no Bedrock generation yet — that is Chapter 10. The vector
dimensions/indexing/similarity semantics this store proves are the query
path's contract.
