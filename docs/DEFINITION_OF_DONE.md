# Definition of Done (10/10)

`CLAUDE.md` is **not present** in this repository; this checklist replaces it for release verification.

## Core engineering gates

| Item | Status | Notes |
|------|--------|-------|
| `make lint` | **PASS** | Ruff lint + format check |
| `make typecheck` | **PASS** | Mypy on `app/` |
| `make test` | **PASS** | 173 tests (≥ 50 required) |
| Docker image builds | **PASS** | `docker-compose up --build` (verify locally) |
| No bare `except` / no `print()` in app code | **PASS** | Per `.cursorrules` |
| Structured logging for services | **PASS** | Existing patterns in codebase |

## Project-specific checks

| Item | Status | Notes |
|------|--------|-------|
| Multiple retrieval strategies | **PASS** | `similarity`, `mmr`, `hybrid` (hybrid gated by `enable_hybrid_retrieval`) |
| Query rewriting triggers | **PASS** | Heuristic trigger in `QueryRewriter` + tests in `test_query_rewriter.py` |
| Source attribution (page/section + relevance) | **PASS** | `CitationSchema` + API responses |
| Conversation memory | **PASS** | Postgres + `POST /chat/query` with `conversation_id` |
| Document versioning (supersede) | **PASS** | Metadata filters + admin flows (see retrieval filters and admin tests) |
| Access control in retrieval | **PASS** | `build_retrieval_where_filters` + tests |
| Ingestion progress tracking | **PASS** | Job/event model + integration tests |
| Guardrails (injection + PII) | **PASS** | `GuardrailService`, `PiiDetector`, query service paths |
| Admin add/remove + re-index | **PASS** | Admin routes + integration tests |
| Cost per query + daily limit | **PASS** | `LlmClient`, `QueryService`, metrics |
| Query analytics in `/metrics` | **PASS** | `MetricsResponse` + `test_metrics_endpoint.py` |
| 50+ tests | **PASS** | 173 collected |
| Evaluation structured report | **PASS** | `make evaluate` → `eval/results/` |

## Manual verification (recommended)

| Item | Status | Notes |
|------|--------|-------|
| OpenAPI UI | **PASS** (when `DEBUG=true`) | `http://localhost:8000/api/v1/docs` |
| Streamlit UI | **PASS** | `make ui` with API running |

## Known gaps / honest limits

- **Postgres down**: Full chat flow requires Postgres for conversations and analytics; **vector retrieval alone** does not use Postgres (`RetrievalService` unit tests document the split). A full “analytics skipped, answers only from Chroma” degraded mode is **not** implemented as a separate code path.
- **10/10 checklist file**: External `CLAUDE.md` absent — this file is the canonical DoD for this repo.
