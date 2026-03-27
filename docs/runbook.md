# Operations Runbook — RAG Knowledge Base Assistant

## Health checks

| Check | Command / URL | Expected |
|-------|-----------------|----------|
| Liveness | `GET /api/v1/health` | `200`, JSON with status and timestamp |
| Readiness | `GET /api/v1/health/ready` | `200` when dependencies satisfied |
| Metrics | `GET /api/v1/metrics` | Aggregates: queries today, cost, refusals, top query hashes |
| OpenAPI | `GET /api/v1/docs` (when `DEBUG=true`) | Swagger UI |

**Streamlit**: open the URL printed by `streamlit run` (default `http://localhost:8501`). Set `RAG_API_BASE` if the API is not on `http://localhost:8000/api/v1`.

---

## Failure diagnosis

### API returns 5xx

1. Check API logs (`docker-compose logs -f app`).
2. Verify Postgres: `db` service healthy in Compose; `DATABASE_URL` matches credentials.
3. Verify Chroma: `chromadb` service healthy; from the app container, Chroma is reachable at `chromadb:8000`.

### Retrieval returns no / weak citations

1. Confirm documents are **indexed** (`documents_indexed` in metrics).
2. Check collection scope: default collections come from the DB catalog; admin APIs can restrict scope.
3. Review `restriction_level` and `user_group` on requests — confidential chunks require an appropriate group.

### LLM errors

1. `OPENAI_API_KEY` set and valid.
2. **Daily cost cap**: when exceeded, calls fail fast with cost-limit errors; check `/api/v1/metrics` for `cost_today_usd` vs `cost_limit_usd`.
3. **Circuit breaker**: repeated timeouts open the breaker; restart the service or wait and retry after addressing upstream issues.

### Streamlit cannot reach API

1. CORS: ensure `CORS_ALLOW_ORIGINS` includes `http://localhost:8501` (Compose default).
2. `RAG_API_BASE` must include the `/api/v1` prefix, e.g. `http://localhost:8000/api/v1`.

---

## Add documents

1. Use **admin** endpoints (see OpenAPI `/api/v1/docs`) to register documents and trigger ingestion, or run ingestion through the pipeline used in tests (`IngestionPipeline`).
2. Supported extensions: `.pdf`, `.docx`, `.md`, `.markdown` (see ingestion pipeline).

---

## Re-index

- **Single document / collection / corpus**: use admin re-index routes (Phase 9) as exposed in `/api/v1` admin router.
- After changing **embedding model** or **chunking** defaults, plan a full re-index; document behavior is described in implementation docs.

---

## Interpret evaluation results

1. Run `make evaluate` (optional `--with-llm` for judge steps that call OpenAI).
2. Artifacts land under `eval/results/` (timestamped reports).
3. Reports include retrieval metrics (e.g. precision/recall where applicable), guardrail outcomes, and optional judge scores — see `app/evaluation/` and `scripts/evaluate.py`.

---

## Common errors and recovery

| Symptom | Likely cause | Recovery |
|---------|--------------|----------|
| `Conversation not found` | Stale `conversation_id` in client | Start a new conversation or list conversations |
| `guardrail_violation` | Injection-like user text | Rephrase; do not paste untrusted instructions |
| `pii_blocked` | PII in query when policy is block | Remove/redact PII |
| `no_collections` | No collections configured | Create collections / ingest docs |
| Port already in use | API and Chroma both on 8000 (host) | Set `CHROMA_PORT=8001` or stop conflicting process |
| Idempotent skip | Same file content hash | Expected; no duplicate vectors for identical content |

---

## Docker Compose ports (reference)

- **App**: `8000` → container `8000` (override with `API_PORT`).
- **Postgres**: `5432` (override with `DB_PORT`).
- **Chroma (host)**: default `8001` → container `8000` (`CHROMA_PORT`).
