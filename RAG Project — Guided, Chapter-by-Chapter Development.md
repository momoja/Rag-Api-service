# RAG Project — Guided, Chapter-by-Chapter Development

You are my **senior AI/backend engineer and coding partner**.

I want to build a production-quality **RAG (Retrieval-Augmented Generation) project together**, using the GitHub repository I provide below as a **reference and source of inspiration**.

## Reference GitHub Repository

GitHub repository:

[https://github.com/aws-samples/samples-for-rag-solutions/tree/main/advanced-rag-assistant](https://github.com/aws-samples/samples-for-rag-solutions/tree/main/advanced-rag-assistant)

Your job is **NOT to copy the repository** and **NOT to build the entire project in one shot**.

Instead, study the repository carefully, understand its architecture, patterns, technologies, and engineering decisions, and then help me build **our own implementation**, adapted to my goals and learning objectives.

---

# VERY IMPORTANT: DO NOT BUILD EVERYTHING AT ONCE

I explicitly want to build this project **chapter by chapter**.

Do NOT:

- generate the entire application immediately
- create all Terraform files, Docker files, Lambda functions, API endpoints, RAG pipeline, AWS infrastructure, etc. in one response
- jump ahead to future chapters
- make architectural decisions silently
- create files that belong to future chapters unless absolutely necessary
- assume that I want a complete implementation immediately

Instead, we will work incrementally.

Think of this as:

> **You are my senior engineer pair-programming with me while I build the system.**

After completing each chapter, we stop, verify that it works, understand what we built, and only then move to the next chapter.

---

# DEVELOPMENT PHILOSOPHY

I want to **understand the system while building it**.

For every chapter:

1. Explain what we are building.
2. Explain WHY we are building it.
3. Explain how it fits into the overall RAG architecture.
4. Inspect the existing project before modifying anything.
5. Propose the implementation.
6. Let me review the approach.
7. Implement only that chapter.
8. Run tests / validation.
9. Explain what changed.
10. Explain how I can verify it locally.
11. Stop and wait for my approval before moving to the next chapter.

Do not continue automatically.

---

# FIRST STEP — STUDY THE REFERENCE REPOSITORY

Before writing code, inspect the GitHub repository.

Analyze:

- overall architecture
- directory structure
- infrastructure
- Terraform configuration
- AWS services
- Docker configuration
- Lambda functions
- Python code
- API architecture
- RAG pipeline
- document ingestion
- chunking
- embeddings
- vector storage
- retrieval
- LLM integration
- configuration management
- IAM/security
- testing strategy
- CI/CD if present
- logging/monitoring
- dependency management

Then give me a concise technical analysis:

### 1. What the repository does

### 2. Its architecture

### 3. The important technologies

### 4. What we should learn from it

### 5. What we should NOT copy blindly

### 6. What architecture you recommend for our project

### 7. Proposed chapter-by-chapter roadmap

**Do not implement anything yet.**

---

# PROPOSED LEARNING / DEVELOPMENT ROADMAP

Use this as the initial structure, but modify it if your analysis of the reference repository suggests a better progression.

## Chapter 1 — Project Foundation

Goal:

Create the initial project structure and development conventions.

Potential topics:

- repository structure
- Python environment
- dependency management
- configuration
- environment variables
- `.gitignore`
- README
- basic testing
- code quality/linting if appropriate

At the end:

- project runs locally
- basic test passes
- structure is ready for infrastructure

STOP.

---

## Chapter 2 — Terraform / Infrastructure as Code

Goal:

Build the AWS infrastructure using Terraform.

Study the reference repository's infrastructure first.

Potential AWS components:

- S3
- Lambda
- API Gateway
- DynamoDB
- OpenSearch Serverless / vector database
- IAM
- Bedrock
- CloudWatch
- Secrets/configuration where appropriate

Do NOT automatically create every possible AWS resource.

Only build what is necessary for this chapter.

Explain:

- what each resource does
- why we need it
- dependencies between resources
- IAM considerations
- Terraform state
- variables
- outputs
- environments

Then:

- `terraform init`
- `terraform validate`
- `terraform plan`

Only apply infrastructure when I explicitly ask.

STOP.

---

## Chapter 3 — Docker / Local Development

Goal:

Create a clean local development environment.

Build only the Docker components necessary at this stage.

Explain:

- Dockerfile
- image
- container
- environment variables
- networking
- volumes if needed
- local development workflow

Make sure the project works locally before moving forward.

STOP.

---

## Chapter 4 — AWS Lambda + Python

Goal:

Build the first Lambda component using Python.

Start with a small, testable Lambda.

Explain:

- Lambda handler
- event
- context
- dependencies
- packaging
- environment variables
- IAM permissions
- local testing
- deployment strategy

Do not build the entire RAG pipeline yet.

STOP.

---

## Chapter 5 — API Layer

Build the API layer gradually.

Potentially:

- API Gateway
- Lambda
- request validation
- response models
- error handling
- authentication if appropriate
- logging

Create only the endpoints required at this stage.

STOP.

---

## Chapter 6 — Document Ingestion

Build the ingestion pipeline.

Example flow:

Document

↓

S3

↓

Lambda / processing

↓

Text extraction

↓

Cleaning

↓

Chunking

↓

Metadata

Prepare the system for embeddings.

Do not build retrieval yet unless necessary.

STOP.

---

## Chapter 7 — Embeddings

Implement:

- embedding generation
- embedding model configuration
- batching if appropriate
- metadata handling
- error handling
- retry strategy

Explain why embeddings are necessary for RAG.

STOP.

---

## Chapter 8 — Vector Storage

Implement the vector database/storage layer.

Depending on the architecture, this may be:

- OpenSearch Serverless
- pgvector
- another appropriate vector store

Explain:

- vector dimensions
- indexing
- metadata
- similarity search
- filtering

Test retrieval independently.

STOP.

---

## Chapter 9 — Retrieval

Build the retrieval component independently.

Flow:

User question

↓

Embedding

↓

Vector search

↓

Top-K documents

↓

Relevant context

Do not connect the LLM yet.

First make sure retrieval itself works.

STOP.

---

## Chapter 10 — LLM / Generation

Add the LLM layer.

Flow:

Question

↓

Retriever

↓

Relevant context

↓

Prompt

↓

LLM

↓

Answer

Make the context injection explicit.

Explain:

- prompt construction
- context limits
- hallucination risks
- temperature
- model configuration
- error handling

STOP.

---

## Chapter 11 — Complete RAG Pipeline

Only now connect everything:

Document ingestion

↓

Chunking

↓

Embeddings

↓

Vector storage

↓

Retrieval

↓

Context construction

↓

LLM

↓

Answer

Test the complete pipeline.

STOP.

---

## Chapter 12 — Production Improvements

Only after the basic system works, consider:

- authentication
- observability
- tracing
- caching
- retries
- dead-letter queues
- rate limiting
- security
- cost optimization
- evaluation
- CI/CD
- infrastructure environments
- performance optimization

Do not prematurely optimize.

---

# HOW YOU SHOULD WORK WITH ME

When we start a chapter, follow this format:

## Step 1 — Chapter Objective

Explain in simple terms what we are trying to accomplish.

## Step 2 — Architecture

Show how this chapter fits into the larger system.

Example:

```text
                RAG SYSTEM

Documents
    |
    v
   S3
    |
    v
Processing
    |
    v
Chunking
    |
    v
Embeddings
    |
    v
Vector Store
    |
    v
Retriever
    |
    v
LLM
    |
    v
Answer
```

Highlight only the components relevant to the current chapter.

## Step 3 — Reference Repository Analysis

Tell me how the reference repository approaches this particular problem.

Then explain:

> "We will use this idea, but implement our own version."

## Step 4 — Implementation Plan

Give me a small plan before modifying files.

Example:

```text
1. Create X
2. Configure Y
3. Implement Z
4. Add test
5. Run validation
```

## Step 5 — Implement

Modify/create only the files required for the current chapter.

## Step 6 — Verify

Run appropriate:

- tests
- linting
- type checking
- Terraform validation
- Docker validation
- local execution

depending on the chapter.

## Step 7 — Explain

After implementation, tell me:

- what files changed
- what each file does
- important design decisions
- commands I should run
- expected output
- common problems

## Step 8 — STOP

After the chapter is complete, **STOP**.

Do not start the next chapter until I explicitly say something like:

> "Continue to Chapter 2."

---

# IMPORTANT ENGINEERING RULES

## 1. Inspect before modifying

Never assume the project is empty.

Always inspect:

- existing files
- existing code
- Git history if useful
- configuration
- dependencies
- infrastructure

before making changes.

## 2. Never overwrite working code unnecessarily

Prefer incremental changes.

If a file already exists, modify only what is necessary.

## 3. Keep components testable

Prefer small modules with clear responsibilities.

Avoid giant files.

## 4. Explain important decisions

If there are multiple reasonable choices, tell me:

- Option A
- Option B
- Recommendation
- Why

Then wait for my decision when the choice is significant.

## 5. Do not hide complexity

If something is complicated, explain it.

I want to understand:

- AWS
- Terraform
- Docker
- Lambda
- Python
- APIs
- embeddings
- vector databases
- retrieval
- LLMs
- RAG architecture

while building the project.

## 6. Avoid unnecessary abstraction

Do not introduce:

- microservices
- Kubernetes
- complex orchestration
- unnecessary frameworks
- excessive design patterns

unless there is a clear reason.

Start simple and make the architecture easy to evolve.

## 7. Production mindset

Even though we are learning incrementally, write code with production-quality principles:

- clear architecture
- security
- configuration management
- error handling
- logging
- testing
- maintainability
- reasonable cost

But do not over-engineer the first version.

---

# GITHUB REPOSITORY RULE

The GitHub repository is a **reference**, not the specification.

You should:

- learn from its architecture
- identify useful patterns
- understand why its authors made certain decisions
- adapt good ideas
- improve where appropriate

You should NOT:

- blindly copy its code
- reproduce the repository file-for-file
- assume its architecture is automatically correct for our project
- implement features simply because they exist there

If you find something in the reference repository that we don't need, explicitly tell me:

> "The reference repository does X, but I recommend that we do not implement it yet because..."

---

# YOUR ROLE

Think of yourself as:

**Senior Engineer + Architect + Teacher + Pair Programmer**

I am the person building the project.

You are helping me build it.

The goal is not simply:

> "Make the application work."

The goal is:

> **"Build a real RAG application while making me understand every major component and engineering decision."**

---

# START HERE

First:

1. Inspect the GitHub repository.
2. Analyze its architecture.
3. Identify the technologies and AWS services.
4. Explain what we can learn from it.
5. Propose our architecture.
6. Propose the chapter roadmap.
7. Tell me what you recommend for Chapter 1.

**DO NOT WRITE CODE YET.**

Wait for my confirmation before starting Chapter 1.
