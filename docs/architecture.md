# System Architecture — RAG Knowledge Base Assistant

## 1. Overview

The RAG Knowledge Base Assistant is a production-grade internal Q&A system for a consulting firm’s private document corpus. It consists of:

- **Ingestion pipeline**: multi-format parsing → normalization → chunking → embedding → vector indexing.
- **Query pipeline**: user input → sanitisation + access control → query rewriting → retrieval + reranking → grounded generation → citation formatting → response.
- **Admin operations**: corpus management, collections/restrictions, document versioning, re-index control, ingestion status.
- **Observability and analytics**: structured logs + correlation IDs, LangSmith tracing, query/audit analytics, and per-request cost tracking.
- **Data stores**: ChromaDB for vectors/chunks; PostgreSQL for admin metadata, ingestion logs, query logs, audit trail, and evaluation runs.

## 2. System Diagram (All Components)

```mermaid
flowchart LR
  %% =========================
  %% Actors
  %% =========================
  Consultant[Consultant]
  Support[Support/Admin Staff]
  Lead[Team Lead]
  KnowledgeManager[Knowledge Manager]

  %% =========================
  %% Entry points
  %% =========================
  subgraph UI[Interfaces]
    StreamlitUI[Streamlit Chat UI]
    AdminUI[Admin UI (Streamlit or internal UI)]
  end

  subgraph API[FastAPI Application]
    ChatRoute[/POST /api/v1/chat/query/]
    AdminDocsRoute[/POST/DELETE /api/v1/admin/documents/]
    AdminCollectionsRoute[/POST/PUT /api/v1/admin/collections/]
    AdminReindexRoute[/POST /api/v1/admin/reindex/]
    HealthRoute[/GET /api/v1/health + /ready + /metrics/]
  end

  %% =========================
  %% Security & guardrails
  %% =========================
  subgraph Security[Security Boundaries]
    AuthZ[AuthN/AuthZ (roles → allowed collections)]
    InputSanitiser[User Input Sanitisation\n(prompt injection detection)]
    PIIDetector[PII Detection/Redaction Policy]
  end

  %% =========================
  %% Query pipeline
  %% =========================
  subgraph QueryPipeline[Query Pipeline (Request-Time)]
    Correlation[Correlation ID Middleware]
    QueryValidator[Pydantic Validation\n(length, schema, content)]
    QueryRewriter[Query Rewriting Service\n(optional)]
    Retriever[Retriever Service\n(similarity/MMR/hybrid)]
    Reranker[Reranker Service\n(cross-encoder or LLM-based)]
    EvidenceSelector[Evidence Thresholding\n(refuse if low support)]
    AnswerGenerator[Answer Generation Service\n(grounded in retrieved chunks)]
    CitationFormatter[Citation Formatting\n(doc, page/section, score)]
    ResponseAssembler[Response Assembler\n(status + data + metadata)]
  end

  %% =========================
  %% Ingestion pipeline
  %% =========================
  subgraph IngestionPipeline[Ingestion Pipeline (Admin/Batch)]
    Upload[Document Upload / Import]
    FormatDetector[Format Detection\n(PDF/DOCX/MD/Notion)]
    Parser[Parsing & Text Extraction\n(pdfplumber/docx/markdown/notion)]
    Normaliser[Text Normalisation\n(clean, dehyphenate, whitespace)]
    Chunker[Chunking Service\n(size + overlap + anchors)]
    Embedder[Embedding Client\n(provider model)]
    VectorWriter[Vector Store Writer\n(Chroma collections)]
    IngestionStatus[Ingestion Status Tracker\n(resumable, per-doc failures)]
    Versioning[Document Versioning Service\n(latest/superseded)]
  end

  %% =========================
  %% External dependencies
  %% =========================
  subgraph External[External Dependencies]
    LLM[LLM Provider API]
    Embeddings[Embedding Provider API]
  end

  %% =========================
  %% Data stores
  %% =========================
  subgraph Stores[Data Stores]
    Chroma[(ChromaDB\nVectors + Chunk Metadata)]
    Postgres[(PostgreSQL\nAdmin + Audit + Analytics)]
  end

  %% =========================
  %% Observability
  %% =========================
  subgraph Observability[Observability]
    StructuredLogging[Structured JSON Logging]
    LangSmith[LangSmith Tracing]
    CostTracker[Token + Cost Tracking]
    QueryAnalytics[Query Analytics\n(top queries, failures)]
    IngestionLogs[Ingestion Logs\n(per doc, per batch)]
  end

  %% =========================
  %% User interactions
  %% =========================
  Consultant --> StreamlitUI
  Support --> StreamlitUI
  Lead --> StreamlitUI
  KnowledgeManager --> AdminUI

  StreamlitUI --> ChatRoute
  AdminUI --> AdminDocsRoute
  AdminUI --> AdminCollectionsRoute
  AdminUI --> AdminReindexRoute

  %% =========================
  %% API wiring
  %% =========================
  ChatRoute --> Correlation --> QueryValidator
  AdminDocsRoute --> Correlation
  AdminCollectionsRoute --> Correlation
  AdminReindexRoute --> Correlation
  HealthRoute --> Correlation

  %% =========================
  %% Security boundaries in query path
  %% =========================
  QueryValidator --> AuthZ
  AuthZ --> InputSanitiser
  InputSanitiser --> PIIDetector
  PIIDetector --> QueryRewriter

  %% Query pipeline steps
  QueryRewriter --> Retriever --> Reranker --> EvidenceSelector
  EvidenceSelector -->|sufficient evidence| AnswerGenerator
  EvidenceSelector -->|insufficient evidence| ResponseAssembler
  AnswerGenerator --> CitationFormatter --> ResponseAssembler

  %% Retrieval storage
  Retriever --> Chroma
  VectorWriter --> Chroma

  %% Generation dependency
  AnswerGenerator --> LLM
  QueryRewriter --> LLM
  Reranker --> LLM
  Embedder --> Embeddings

  %% =========================
  %% Ingestion path
  %% =========================
  AdminDocsRoute --> Upload --> FormatDetector --> Parser --> Normaliser --> Chunker
  Chunker --> PIIDetector --> Embedder --> VectorWriter
  Upload --> Versioning
  Parser --> IngestionStatus
  VectorWriter --> IngestionStatus
  Versioning --> Postgres
  IngestionStatus --> Postgres

  %% =========================
  %% Observability wiring
  %% =========================
  Correlation --> StructuredLogging
  QueryPipeline --> StructuredLogging
  IngestionPipeline --> StructuredLogging
  QueryPipeline --> LangSmith
  IngestionPipeline --> LangSmith
  QueryPipeline --> CostTracker
  QueryPipeline --> QueryAnalytics
  IngestionPipeline --> IngestionLogs
  CostTracker --> Postgres
  QueryAnalytics --> Postgres
  StructuredLogging --> Postgres
  IngestionLogs --> Postgres

  %% Responses
  ResponseAssembler --> ChatRoute
```

## 3. Component Responsibilities (Concrete)

### 3.1 FastAPI (API surface)

- **Chat route**: validates request, resolves user identity/roles, delegates to query service, returns response with citations and metadata.
- **Admin routes**: document lifecycle (add/remove), collection lifecycle, re-index triggers, and ingestion status.
- **Health/metrics**: liveness, readiness (dependency checks), and operational metrics (latency, errors, cost, ingestion backlog).

### 3.2 Query services (business logic)

- **Input sanitisation**: rejects/flags known prompt injection patterns; enforces length and content constraints.
- **Access control filter**: converts user roles → allowed collections; enforces collection filters at retrieval time.
- **Query rewriting** (optional): produces a retrieval-optimized query (and optionally sub-queries) without changing user intent.
- **Retrieval**: executes similarity/MMR/hybrid against ChromaDB scoped to authorized collections.
- **Reranking**: reorders retrieved chunks by relevance to reduce “near-miss” citations.
- **Evidence thresholding**: if top-k does not meet relevance or coverage thresholds, returns a refusal.
- **Generation**: answers only from retrieved chunks; refuses if the model attempts to go beyond evidence.
- **Citation formatting**: attaches document identifiers and anchors (page/section/heading) plus relevance score.

### 3.3 Ingestion services (batch + admin)

- **Format detection**: determines parser strategy (PDF/DOCX/MD/Notion export).
- **Parsing + extraction**: extracts text and structural anchors (page numbers/headings when possible).
- **Normalization**: cleans text to improve chunk quality (consistent whitespace, dehyphenation, removal of boilerplate).
- **Chunking**: produces chunks with overlap and provenance metadata (doc_id, page/section, offsets).
- **PII handling prior to embedding**: applies configured policy (block/redact) before any text is sent externally.
- **Embedding + indexing**: generates embeddings and writes to ChromaDB collections; writes ingestion state to PostgreSQL.
- **Versioning**: maintains “latest” vs “superseded” to prevent outdated answers.
- **Resumability**: ingestion is checkpointed; failures are per-document and do not fail the full batch.

### 3.4 Observability and analytics

- **Structured logging**: JSON logs with correlation IDs; safe previews only.
- **LangSmith tracing**: traces ingestion and query chains for debugging, quality, and latency tuning.
- **Cost tracking**: token usage and cost stored per request; daily aggregation and budget enforcement.
- **Query analytics**: top queries, failed retrievals, refusal categories, latency distribution, cost distribution.

## 4. Data Flow — Single Query (User → Final Response)

This traces every transformation from inbound question to outbound answer.

1. **User submits question**
   - Source: Streamlit UI → `POST /api/v1/chat/query`.
   - Payload (validated): `question`, `conversation_id` (optional), `top_k`, `collections_hint` (optional).

2. **Request normalization**
   - Correlation ID created (or accepted from `X-Correlation-ID`) and attached to all logs/traces.
   - Request model validation: length limits, required fields, encoding normalization.

3. **Identity and authorization**
   - Resolve user identity and roles (e.g., via token claims).
   - Compute **allowed collections**; attach as an immutable filter for retrieval.

4. **Input sanitisation + prompt injection defense**
   - Scan for prompt injection patterns (e.g., “ignore previous instructions”, role tags, tool injection markers).
   - If high-risk: refuse with a security-safe message and log event category.

5. **PII detection policy (query-time)**
   - Detect if the user question contains PII (or requests restricted client info).
   - Apply configured policy:
     - block (refuse), or
     - redact sensitive fragments before any LLM call.

6. **Query rewriting (optional)**
   - Transform user question into retrieval-optimized query (e.g., add synonyms, expand acronyms, split into sub-queries).
   - Output: `rewritten_query` plus trace metadata (version, model, latency, cost).

7. **Retrieval**
   - Execute retrieval against ChromaDB:
     - similarity search and/or
     - MMR diversification and/or
     - hybrid (dense + keyword/BM25-like) when configured.
   - Apply filters:
     - allowed collections
     - restriction flags
     - versioning preference (latest unless explicitly requested).
   - Output: top-k chunk candidates with scores and provenance.

8. **Reranking**
   - Re-rank retrieved chunks for semantic relevance to the question.
   - Output: ordered evidence set with updated relevance scores.

9. **Evidence thresholding**
   - Compute evidence sufficiency:
     - minimum top-1 score
     - coverage across sub-questions
     - redundancy/contradiction checks.
   - If insufficient: refuse (and optionally return suggested rephrase + what collections were searched).

10. **Grounded generation**
   - Construct system prompt (instructions + safety) and user prompt (question + evidence snippets).
   - Generate an answer constrained to the evidence.
   - Validate output for:
     - forbidden content (restricted/PII leakage)
     - citation completeness.

11. **Citation formatting**
   - For each claim/citation: attach `doc_title`, `doc_id`, `page/section`, and relevance score.
   - Output: `answer_text` + `citations[]`.

12. **Response assembly**
   - Return consistent API envelope including:
     - answer
     - citations
     - metadata (latency, tokens, cost, model, retrieval stats, correlation_id).

13. **Analytics/audit write**
   - Write query event to PostgreSQL (if available):
     - normalized question hash
     - retrieval stats
     - refusal reason (if any)
     - cost + latency.
   - If PostgreSQL is unavailable: return response anyway; log degraded analytics.

## 5. Data Flow — Single Document (Upload → Queryable)

This traces every step from new content entering the system to being retrievable.

1. **Admin uploads/imports document**
   - Source: Admin UI or Admin API.
   - Inputs: file bytes / export bundle, collection assignment(s), restriction level, version label, optional “supersedes doc_id”.

2. **Document registration (PostgreSQL)**
   - Create `document` record: `doc_id`, title, source, collections, restriction flags, version metadata.
   - Set ingestion status: `pending`.

3. **Format detection**
   - Identify document type (PDF/DOCX/Markdown/Notion export).
   - Select parsing strategy and parsing configuration.

4. **Parsing and text extraction**
   - Extract text and structural anchors:
     - PDF: page numbers + text
     - DOCX: headings, paragraphs
     - Markdown: headings, sections
     - Notion export: block structure → headings/sections.
   - On failure: record per-document error in ingestion log and continue batch.

5. **Normalization**
   - Clean the extracted text:
     - consistent whitespace
     - remove repeating headers/footers (where detected)
     - dehyphenation (PDF artifacts)
     - optional language detection metadata.

6. **Chunking**
   - Split normalized text into chunks with overlap.
   - For each chunk, generate provenance metadata:
     - doc_id
     - page/section anchor
     - offsets and chunk index
     - collection and restriction flags.

7. **PII detection before embedding**
   - Scan chunk text for PII.
   - Apply policy:
     - redact PII before embedding, or
     - block indexing of that chunk/document if required.
   - Record policy decision and counts in PostgreSQL.

8. **Embedding**
   - Send chunk text to embedding provider API.
   - Collect embedding vectors and per-call metrics (latency, tokens/cost if applicable).

9. **Vector store write (ChromaDB)**
   - Upsert embeddings into Chroma collections per practice area/team.
   - Store chunk metadata for later citation formatting and filtering.

10. **Ingestion finalization**
   - Update ingestion status to `indexed` and store counts:
     - pages, chunks, redactions, failures.
   - If this document supersedes another: mark older doc as `superseded` and adjust retrieval preference.

11. **Document becomes queryable**
   - Retrieval layer can now return chunks from this doc (subject to access control and restriction flags).

## 6. Failure Paths (External Dependencies)

This section specifies how the system behaves when dependencies fail, with explicit recovery and user-facing outcomes.

### 6.1 LLM API unavailable (generation / rewriting / reranking)

- **Detection**
  - timeout, 429, 5xx, network errors.
- **Recovery**
  - retry with exponential backoff + jitter for retryable classes (timeouts, 5xx, 429 with `retry-after`).
  - circuit breaker opens after configurable consecutive failures to prevent request storms.
- **User-facing outcome**
  - graceful error: “AI provider temporarily unavailable; please retry.”
  - if possible: return retrieval-only results (citations list) without generated synthesis.
- **Observability**
  - log warning/error with correlation ID, provider, attempt count, breaker state, and latency.
  - record degraded-mode event in analytics when PostgreSQL is available.

### 6.2 ChromaDB query fails (retrieval)

- **Detection**
  - connection errors, timeouts, internal server errors.
- **Recovery**
  - short retry (bounded) if transient; otherwise fail fast.
  - circuit breaker for ChromaDB connection failures if deployed as a network service.
- **User-facing outcome**
  - graceful error: “Search index temporarily unavailable; unable to retrieve sources.”
  - no generation attempt is made (prevents hallucination).
- **Observability**
  - error log includes collection filter, top_k, and failure type (no sensitive content).

### 6.3 Document parsing fails (ingestion)

- **Detection**
  - parser exceptions per file type (corrupt PDF, unsupported DOCX features, malformed Notion export).
- **Recovery**
  - per-document failure logging; mark document as `failed` with error code and stack trace hash.
  - continue batch ingestion; provide admin summary (failed count, reasons).
  - resumable ingestion: admin can retry failed documents only after fixes.
- **User-facing outcome**
  - admin sees ingestion status + failure reason; document is not queryable.
- **Observability**
  - ingestion logs include doc_id, parser type, failure category, and duration.

### 6.4 PostgreSQL unavailable (admin + analytics)

- **Detection**
  - connection pool exhaustion, network errors, migration mismatch, timeouts.
- **Degraded-mode policy**
  - **Queries still work**: retrieval + generation continue, but analytics/audit writes are skipped.
  - **Admin operations are limited**: document registration/versioning requires PostgreSQL; admin endpoints return a clear error.
- **User-facing outcome**
  - chat continues with a “degraded analytics” internal state (not shown to end users unless desired).
  - admin endpoints respond with “admin datastore unavailable; try later.”
- **Observability**
  - critical log with correlation ID and dependency health state.

## 7. Security Boundaries (Threat-Driven)

### 7.1 User input sanitised before reaching LLM

- Validate and normalize user input at the API boundary.
- Detect and block prompt injection patterns before:
  - query rewriting calls
  - reranking calls
  - answer generation calls.
- Separate prompts:
  - **system prompt**: non-user-controlled instructions
  - **user prompt**: question and evidence only.

### 7.2 PII detection on document content before embedding

- Chunk text is scanned for PII before any external embedding call.
- Policy is configurable per deployment:
  - redact PII in chunks before embedding,
  - or block indexing for restricted documents/chunks.
- PII artifacts are never logged verbatim; only counts and categories are recorded.

### 7.3 Access control between collections

- Each document is assigned to one or more **collections** (practice area/team).
- Each user role maps to an allowed set of collections.
- Retrieval enforces:
  - collection filters
  - restriction flags
  - versioning preference (latest vs superseded).

### 7.4 Prompt injection patterns blocked

- Block known patterns and suspicious role/tool markers.
- Refuse requests attempting to:
  - override system instructions,
  - request hidden prompts,
  - exfiltrate restricted content,
  - or coerce citations unrelated to retrieval results.
- Log security events with correlation ID and detection category (not the raw content).

## 8. Architecture Notes (Design Choices That Matter)

- **Grounding rule**: generation is never attempted without retrieved evidence; the system refuses instead of guessing.
- **Versioning-aware retrieval**: retrieval prefers “latest” documents to reduce outdated answers, while allowing explicit historical queries when permitted.
- **Degraded analytics mode**: PostgreSQL outages should not take down query answering; they reduce observability until recovered.
- **Resumable ingestion**: ingestion is built for large corpora and partial failures, with per-document fault isolation.

