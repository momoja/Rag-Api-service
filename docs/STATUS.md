# Project Status — 2026-09-03

Handoff record so any session (or clone) can resume Chapter 3 without prior
conversation context. Pair with the git log and `docs/decisions/` for decisions.

## Current state

- **Chapter 1 — Foundation: DONE** (commit `684fc89`). uv-managed Python 3.12
  project (`rag-agent`), `pytest` + `ruff`, hatchling backend, ADR 0001.
- **Chapter 2 — Terraform: DONE, NOT APPLIED** (commit `bb8b87c`). `infra/dev/`
  defines the documents S3 bucket only. `terraform plan` verified:
  **4 to add, 0 to change, 0 to destroy**. No AWS resources exist yet.
- **Next: Chapter 3 — Docker / local development.** User said "we will continue
  to chapter 3 ultérieurement" (later).

## Verified environment facts (2026-09-03)

- OS: Windows 11; shell/cmd via this harness (PowerShell-friendly commands).
- git 2.53, uv 0.12.9 (manages Python 3.12.9 in `.venv`), Python 3.14 system.
- Terraform v1.16.0, AWS CLI 2.36.30.
- AWS creds valid: account `058264314263`, IAM user `terraform-myapp`.
- Region decision: **us-east-1** (Bedrock/AOSS coverage; CLI default was
  us-west-1 — do not use).

## Decisions binding later chapters

1. **Self-managed RAG core on AWS** (ADR 0001): we write ingestion → chunking →
   embeddings → retrieval → generation; AWS supplies S3/Lambda/Bedrock models/
   vector store. Bedrock Knowledge Bases (managed) explicitly rejected.
2. Region: us-east-1 everywhere.
3. Terraform state: **local now**; when the user approves the first `apply`,
   bootstrap S3 remote state + DynamoDB lock table, flip backend,
   `init -migrate-state`, then apply. Apply must be explicitly requested.
4. IAM deferred from ch2 to ch4 (nothing to run yet) — deliberate.
5. Vector store choice deferred to ch8 (AOSS vs pgvector); embedding dimension
   contract (Titan v2 = 1024) must drive the index mapping.
6. Package `rag_agent` (not `app`) to avoid Lambda package collisions.

## Verify commands

```powershell
uv run pytest
terraform -chdir=infra/dev fmt -check -recursive
terraform -chdir=infra/dev validate
terraform -chdir=infra/dev plan -var-file=dev.tfvars
```

## Chapter 3 preview (from guide + reference analysis)

Dockerfile(s) for local dev parity with the Lambda runtime (`python:3.12`),
compose file, volume/environment conventions. Reference repo has no root
Docker; sibling variants Dockerize Streamlit only — we adapt, don't copy.
