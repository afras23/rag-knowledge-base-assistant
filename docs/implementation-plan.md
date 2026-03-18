# Implementation Plan — RAG Knowledge Base Assistant (12 Phases / 12 Commits)

This plan is sized for **20–28 total hours**, broken into **12 phases** of **1–3 hours each**.  
Each phase produces **exactly one commit** and has **acceptance criteria** that can be verified locally via Docker and tests.

**ADR alignment (non-negotiable):**

- **Vector DB**: ChromaDB (`ADR 001`)
- **Embeddings**: `bge-small` default (local-first), configurable OpenAI option (`ADR 006`)
- **LLM**: OpenAI **GPT-4o** default, GPT-4.1 optional (`ADR 007`)
- **Retrieval**: hybrid approach, **MMR default** + query rewriting for complex questions (`ADR 002`)
- **Chunking**: recursive splitting (1000 chars, 200 overlap) + header-aware for MD/DOCX (`ADR 003`)
- **Guardrails**: retrieval-grounded generation + minimum relevance threshold + injection detection + refusal (`ADR 004`)
- **Evaluation**: retrieval metrics + LLM-as-judge + citation verification (`ADR 005`)

---

## Phase 1 (1–3 hours): Project scaffold + local stack

### Checklist

- [ ] Create FastAPI app skeleton with app factory and `/api/v1` routing
- [ ] Add `/api/v1/health`, `/api/v1/health/ready`, `/api/v1/metrics` route stubs
- [ ] Add Pydantic Settings-based config (`.env.example` included)
- [ ] Add Dockerfile (multi-stage, non-root, healthcheck) and `docker-compose.yml` with:
  - [ ] FastAPI service
  - [ ] PostgreSQL service (healthcheck + named volume)
- [ ] Add GitHub Actions CI workflow (ruff + mypy + pytest)
- [ ] Add baseline project tooling config (ruff/mypy/pytest in `pyproject.toml` or equivalent)

### Commit message

`chore: scaffold FastAPI stack with Docker and CI`

### Acceptance criteria

- `docker-compose up --build` starts FastAPI + Postgres successfully
- `GET /api/v1/health` returns healthy JSON with timestamp and version
- CI runs lint/typecheck/tests (even if tests are minimal at this stage)

---

## Phase 2 (1–3 hours): Domain models + database schema + first migration

### Checklist

- [ ] Define Pydantic boundary models for:
  - [ ] chat query request/response (including citations shape)
  - [ ] admin document create/remove
  - [ ] collection definitions and access rules (roles → collections)
- [ ] Define SQLAlchemy models for PostgreSQL tables:
  - [ ] `documents` (metadata, restriction flags, versioning fields, supersedes pointer)
  - [ ] `document_ingestion_jobs` (batch/job status, progress counters)
  - [ ] `document_ingestion_events` (per-document/per-stage errors + timings)
  - [ ] `query_events` (question hash, user/group, collections searched, retrieval stats, refusal reason)
  - [ ] `llm_call_audit` (model, prompt version, token usage, cost, latency)
  - [ ] `evaluation_runs` (config snapshot + metrics)
- [ ] Add Alembic and create **initial migration** for the above
- [ ] Add repository interfaces for persisting/querying analytics and admin metadata (no raw SQL in routes)

### Commit message

`feat: add domain models and initial Postgres schema`

### Acceptance criteria

- Alembic migration applies cleanly against a fresh Postgres container
- Basic repository calls can create/read a `documents` record and a `query_events` record in an async test

---

## Phase 3 (1–3 hours): Document ingestion pipeline (multi-format) + progress tracking

### Checklist

- [ ] Implement parsers:
  - [ ] PDF parsing via `pdfplumber` (extract text per page + page anchors)
  - [ ] DOCX parsing via `python-docx` (extract headings + paragraphs; preserve header anchors)
  - [ ] Markdown parsing (native read; header anchors)
- [ ] Implement normalization pipeline (whitespace, dehyphenation for PDFs, boilerplate trimming)
- [ ] Implement chunking per `ADR 003`:
  - [ ] recursive char splitter: 1000 chars, 200 overlap (configurable)
  - [ ] header-aware splitting for DOCX/Markdown sections before recursive splitting
- [ ] Implement ingestion job tracking:
  - [ ] batch/job record with totals + processed/succeeded/failed counters
  - [ ] per-document status and per-stage error logs in Postgres
  - [ ] resumable ingestion (skip already-indexed docs by idempotency key/content hash)
- [ ] Wire ingestion service to write document metadata (collections, restrictions, version label, supersedes)

### Commit message

`feat: implement multi-format ingestion with chunking and job tracking`

### Acceptance criteria

- Ingesting a mixed batch (PDF + DOCX + MD) produces:
  - chunk artifacts with correct anchors (page/heading)
  - job progress counters in Postgres
  - per-document failures recorded without aborting the batch

---

## Phase 4 (1–3 hours): Embeddings + ChromaDB indexing (ADR-aligned)

### Checklist

- [ ] Implement embedding provider abstraction per `ADR 006`:
  - [ ] default: local `bge-small` embeddings
  - [ ] optional: OpenAI embeddings via config
- [ ] Implement ChromaDB client wrapper per `ADR 001`:
  - [ ] collections map to practice-area/team collections
  - [ ] metadata schema supports filters (collection, restriction, doc_id, version fields)
  - [ ] upsert API is idempotent for re-indexing
- [ ] Write indexing service:
  - [ ] embed chunks (with concurrency limits)
  - [ ] upsert into Chroma with metadata needed for citations and access control
  - [ ] persist embedding backend name/version used for the index build in Postgres ingestion logs

### Commit message

`feat: add embedding providers and ChromaDB indexing`

### Acceptance criteria

- After ingestion, chunks are queryable in Chroma with metadata filters working
- Switching embedding provider in config triggers a full re-index path (documented behavior) without code changes

---

## Phase 5 (1–3 hours): Retrieval pipeline (multi-strategy + query rewriting)

### Checklist

- [ ] Implement retrieval service per `ADR 002`:
  - [ ] similarity search
  - [ ] MMR (default) with configurable diversity parameter
  - [ ] hybrid retrieval strategy (dense + keyword/BM25-like where supported), behind a feature flag/config
- [ ] Implement query rewriting (optional) with strict guardrails:
  - [ ] only for complex/ambiguous queries based on a deterministic heuristic trigger
  - [ ] log rewritten query and strategy used
- [ ] Implement relevance scoring outputs:
  - [ ] include retrieval score per chunk
  - [ ] attach provenance for citations (doc_id + page/section)
- [ ] Enforce access control filters in retrieval (collections and restriction flags)

### Commit message

`feat: implement MMR-first retrieval with hybrid option and query rewriting`

### Acceptance criteria

- Retrieval returns top-k chunks with scores and anchors
- Access control filters prevent retrieval from unauthorized collections
- Query rewriting is invoked only when trigger conditions are met and is traceable in logs

---

## Phase 6 (1–3 hours): Generation + citation formatting + confidence scoring

### Checklist

- [ ] Implement grounded generation per `ADR 007` and `ADR 004`:
  - [ ] GPT-4o default for answer synthesis
  - [ ] prompt explicitly forbids using knowledge outside retrieved chunks
  - [ ] model + prompt version recorded per request
- [ ] Implement citation formatting:
  - [ ] include `document_title`, `doc_id`, `page/section`, `relevance_score`
  - [ ] normalize anchors across formats (PDF page vs Markdown/DOCX headings)
- [ ] Implement answer confidence scoring:
  - [ ] based on evidence sufficiency + score distribution (composite, deterministic)
  - [ ] low confidence forces refusal or “insufficient evidence” response (policy-driven)

### Commit message

`feat: add grounded answer generation with citations and confidence scoring`

### Acceptance criteria

- Responses include an answer plus structured citations with anchors and relevance scores
- If evidence is weak, the system refuses rather than guessing (verified by test)

---

## Phase 7 (1–3 hours): Guardrails (injection, PII, out-of-scope, thresholds)

### Checklist

- [ ] Implement prompt injection detection on user input:
  - [ ] block known patterns and suspicious role/tool markers
  - [ ] log security event category (not raw content)
- [ ] Implement minimum relevance thresholding:
  - [ ] refuse if top-k scores do not meet evidence threshold
  - [ ] refusal response includes what was searched and suggested rephrase (no leakage)
- [ ] Implement PII detection policies:
  - [ ] ingestion-time PII scan before embeddings (block/redact per config)
  - [ ] query-time PII scan before LLM calls (block/redact per config)
- [ ] Ensure generation is never attempted without evidence

### Commit message

`feat: implement guardrails for injection, PII, and evidence-based refusal`

### Acceptance criteria

- Injection-like inputs are refused and never reach the LLM client
- Documents/chunks flagged by PII policy are handled per config (redact/block) and recorded in ingestion logs
- Out-of-scope queries refuse with a consistent response format

---

## Phase 8 (1–3 hours): Conversation memory + API endpoints

### Checklist

- [ ] Implement conversation persistence (PostgreSQL):
  - [ ] `conversations` and `conversation_messages` tables (Alembic migration)
  - [ ] conversation context windowing rules (only safe, necessary history)
- [ ] Add FastAPI endpoints:
  - [ ] `POST /api/v1/chat/query` (multi-turn support)
  - [ ] `GET /api/v1/chat/conversations/{id}` (history with citations)
  - [ ] health/ready/metrics endpoints return real dependency status and baseline metrics
- [ ] Ensure conversation context is sanitized and does not cross user boundaries

### Commit message

`feat: add conversation memory and chat/history endpoints`

### Acceptance criteria

- Follow-up questions use prior context correctly (validated by an integration test)
- Conversation history endpoint returns messages with citations and timestamps

---

## Phase 9 (1–3 hours): Admin interface + versioning + re-index operations

### Checklist

- [ ] Implement admin API endpoints:
  - [ ] add/remove documents
  - [ ] manage collections and restrictions
  - [ ] trigger re-index for:
    - [ ] single document
    - [ ] collection
    - [ ] full corpus
  - [ ] view ingestion job status + per-document errors
- [ ] Implement document versioning per requirements:
  - [ ] mark document as superseded
  - [ ] retrieval preference for “latest” by default
  - [ ] admin can override and query historical versions (role-gated)
- [ ] Enforce pagination for admin list endpoints (no unbounded lists)

### Commit message

`feat: add admin endpoints for corpus management, versioning, and reindex`

### Acceptance criteria

- Admin can add a document, see ingestion progress, and trigger re-index
- Superseded documents are not used by default retrieval when a newer version exists

---

## Phase 10 (1–3 hours): Evaluation pipeline (50+ Q&A) + make evaluate

### Checklist

- [ ] Create evaluation dataset format (JSONL) with **50+** questions:
  - [ ] includes multi-turn cases, out-of-scope cases, restricted-access cases
  - [ ] includes injection attempts for guardrail regression checks
- [ ] Implement retrieval evaluation per `ADR 005`:
  - [ ] precision@k, recall@k (and optionally MRR/nDCG)
  - [ ] store per-question retrieval outcomes for debugging
- [ ] Implement answer quality evaluation:
  - [ ] LLM-as-judge with strict rubric (groundedness, correctness, completeness)
  - [ ] configurable and cached where possible to control cost
- [ ] Implement citation accuracy evaluation:
  - [ ] exact citation matching when anchors are deterministic
  - [ ] automated verification that cited chunk contains supporting text (where feasible)
- [ ] Add `make evaluate` (or equivalent) to run and write timestamped results into `eval/results/`

### Commit message

`feat: add evaluation pipeline with retrieval, answer, and citation metrics`

### Acceptance criteria

- `make evaluate` runs end-to-end and produces a structured report artifact
- Report includes: overall accuracy, precision@k, recall@k, citation accuracy, avg latency, avg cost per query

---

## Phase 11 (1–3 hours): Observability (correlation IDs, LangSmith, cost, analytics, metrics)

### Checklist

- [ ] Implement correlation ID middleware (propagate to logs + response header)
- [ ] Implement structured JSON logging across:
  - [ ] ingestion pipeline stages
  - [ ] retrieval and reranking
  - [ ] generation and refusals
- [ ] Integrate LangSmith tracing for ingestion + query chains
- [ ] Implement cost tracking per query:
  - [ ] token usage captured for each LLM call
  - [ ] aggregate daily cost in Postgres
  - [ ] enforce daily configurable cost cap (refuse or degrade when exceeded)
- [ ] Implement query analytics in Postgres:
  - [ ] top queries (by hash), refusal categories, failure counts
- [ ] Implement `/api/v1/metrics` with real data (not placeholders)

### Commit message

`feat: add observability, LangSmith tracing, and cost/metrics analytics`

### Acceptance criteria

- Every request returns `X-Correlation-ID` and all logs include correlation ID
- `/api/v1/metrics` returns real counts and cost aggregates from Postgres
- Cost cap is enforced (verified by test with mocked cost totals)

---

## Phase 12 (1–3 hours): Chat UI + final testing + documentation polish

### Checklist

- [ ] Build Streamlit chat UI:
  - [ ] multi-turn conversation selector
  - [ ] render citations with anchors and relevance scores
  - [ ] display refusal reasons in a user-friendly way
- [ ] Expand automated tests to **50+**, including:
  - [ ] retrieval strategy tests (MMR diversity behavior)
  - [ ] guardrail security tests (prompt injection)
  - [ ] idempotent ingestion tests
  - [ ] degraded-mode tests (Postgres down → queries still respond; analytics skipped)
  - [ ] external dependency failure tests (LLM down → retries + breaker + graceful error)
- [ ] Documentation polish:
  - [ ] update README into case study format including evaluation results
  - [ ] add `docs/runbook.md` (ops playbook: health checks, common failures, recovery)
  - [ ] verify ADRs are complete and referenced where relevant
  - [ ] ensure Docker builds clean and CI passes
  - [ ] confirm 10/10 checklist items are satisfied for this project

### Commit message

`feat: add Streamlit UI and finalize tests/docs for 10/10 delivery`

### Acceptance criteria

- Streamlit UI supports multi-turn Q&A and displays citations cleanly
- Test suite passes locally and in CI; total tests ≥ 50
- `docker-compose up --build` works from scratch and app is usable within 5 minutes
- README includes evaluation results and “How to Run”; runbook exists

