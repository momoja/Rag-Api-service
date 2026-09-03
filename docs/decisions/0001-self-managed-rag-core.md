# ADR 0001 — Self-managed RAG core on AWS

- Status: Accepted
- Date: 2026-09-03

## Context

We are building a production-quality RAG application, chapter by chapter, with an
explicit learning goal: understand every major component (ingestion, chunking,
embeddings, vector storage, retrieval, generation). Reference material:
`aws-samples/samples-for-rag-solutions/advanced-rag-assistant`.

That sample outsources the entire RAG pipeline to Amazon Bedrock Knowledge Bases —
a managed black box we would only *configure*. Under that model, retrieval quality,
chunking behavior, and prompt construction are invisible and untestable in our code.

## Decision

Build a **self-managed RAG core on AWS**: we write ingestion, chunking, embedding,
retrieval, and generation code ourselves. AWS stays the platform — S3 for storage,
Bedrock for embedding and LLM models, a managed vector store (decided in Chapter 8) —
but every RAG stage is our code, testable in isolation.

Rejected options:

- **Managed Bedrock Knowledge Bases** (reference-style): fastest and cheapest to
  operate, but the pipeline becomes console configuration and the learning goal is lost.
- **Hybrid** (self-managed now, Bedrock KB comparison as a capstone): still viable
  later as a validation exercise; not needed for the initial build.

## Consequences

- More code to write and maintain, but each stage is inspectable and testable.
- Embedding model choice is a hard contract (vector dimension) that the vector-store
  chapter must honor — per the reference's model-to-index mapping pattern.
- Model and vector-store APIs stay managed; no GPU/self-hosted infrastructure.
- Chapter 2 (Terraform) starts with S3 + IAM only; RAG infrastructure is added
  incrementally as each pipeline stage lands.
