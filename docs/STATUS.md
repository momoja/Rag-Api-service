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
- **Chapter 6 — Document ingestion: DONE** (working tree, uncommitted —
  commit on move-on). uploads/ key policy enforced in `rag_agent/storage.py`
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
- **Next: Chapter 7 — Embeddings** (reads `processed/<doc_id>/chunks.jsonl`
  + `metadata.json` from Chapter 6's output).

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

## Verify commands

```powershell
uv run pytest                      # 94 passed
uv run ruff check . && uv run ruff format --check .
terraform -chdir=infra/dev validate
terraform -chdir=infra/dev plan -var-file=dev.tfvars   # 22 to add
docker compose build && docker compose run --rm app    # dev image tests (94)
docker run --rm -e DOCUMENTS_BUCKET=<bucket> -v "$env:USERPROFILE\.aws:/root/.aws:ro" `
  --entrypoint python rag-agent-presign:dev -c "<v2-event script>"  # 200/400/400/400
```

## Chapter 7 preview

Embeddings over Chapter 6 output: read each `processed/<document_id>/`
chunks.jsonl + metadata.json, embed chunks with Amazon Titan Text Embeddings
v2 (1024-dim contract, decision 5) with batching/retry, then store vectors
in the (Chapter 8) vector store. Real end-to-end ingestion smoke test (PUT
a file to uploads/, watch the pipeline stage output) lands at the first
apply — no S3 resources exist yet to exercise.
