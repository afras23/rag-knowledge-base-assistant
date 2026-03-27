# Changelog

All notable changes to this project are described by **delivery phase** (see `docs/implementation-plan.md`).

## Phase 12 — Streamlit UI, expanded tests, documentation

- Streamlit chat UI (`frontend/app.py`): conversation sidebar, citations, refusals, loading and error states; `make ui` target; `streamlit` + `httpx` in `requirements.txt`.
- OpenAPI: Swagger UI and OpenAPI JSON mounted under `/api/v1/docs` and `/api/v1/openapi.json` when `DEBUG=true`.
- Additional tests: hybrid merge, MMR diversity, hybrid feature flag, retrieval latency budget, Chroma-only retrieval contract, guardrail injection cases, idempotent ingestion skip, non-retryable LLM errors, OpenAPI route smoke test.
- Documentation: `README.md` (case study), `docs/runbook.md`, `docs/DEFINITION_OF_DONE.md`.
- Docker Compose: default **host** port for Chroma mapped to **8001** to avoid conflicting with the API on **8000**.

## Phase 11 — Observability

- Correlation IDs, structured logging, LangSmith optional wrapping, cost tracking and daily limits, query analytics, `/api/v1/metrics`.

## Phase 10 — Evaluation

- Evaluation pipeline (`app/evaluation/`), JSONL test set, `make evaluate`, structured reports under `eval/results/`.

## Phase 9 — Admin

- Admin endpoints for documents, collections, re-index operations, ingestion job visibility, versioning/supersede semantics.

## Phase 8 — Conversations + chat API

- `POST /api/v1/chat/query`, `GET /api/v1/chat/conversations`, conversation detail with message history.

## Phase 7 — Guardrails

- Prompt injection detection, PII policies, relevance thresholds, consistent refusal payloads.

## Phase 6 — Generation

- Grounded generation, citation schema, confidence scoring.

## Phase 5 — Retrieval

- Similarity, MMR, hybrid (optional), query rewriting, access-control filters in Chroma metadata.

## Phase 4 — Embeddings + indexing

- Embedding providers, Chroma client wrapper, indexing service.

## Phase 3 — Ingestion

- PDF/DOCX/Markdown parsers, chunking, ingestion jobs and events, content-hash idempotency.

## Phase 2 — Domain + database

- SQLAlchemy models, Alembic migrations, repositories.

## Phase 1 — Scaffold

- FastAPI app, health/metrics routes, Docker, CI, tooling.
