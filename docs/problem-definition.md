# Problem Definition — RAG Knowledge Base Assistant

## 0. Executive Summary

A mid-size consulting firm (50–200 employees) has critical institutional knowledge scattered across 500–800 documents (PDF, DOCX, Markdown wiki, Notion exports) stored in multiple systems. Staff currently spend 15–30 minutes per question searching Slack history, Drive/SharePoint folders, and multiple documents—20–40 times per day.

This project builds a **production-grade Retrieval-Augmented Generation (RAG) system** that answers internal questions with **verifiable citations**, supports **multi-turn chat**, enforces **access control**, detects **PII before LLM calls**, mitigates **prompt injection**, and provides **admin operations** (ingest, versioning, re-index) plus **analytics** (failed queries, cost per query, top topics).

## 1. Business Context

### 1.1 Organization profile

- **Firm size**: 50–200 employees (consultants + support/admin).
- **Practice areas**: strategy, operations, compliance, finance.
- **Current knowledge footprint**: 500–800 documents across heterogeneous formats and storage locations.
- **Operational pain**: internal questions repeatedly interrupt senior staff and support/admin.

### 1.2 Why this matters

- **Time waste**: 15–30 minutes per question × 20–40 questions/day = **5–20 staff-hours/day** spent on searching and re-answering.
- **Inconsistent answers**: different people reference different versions or incomplete sources.
- **Institutional risk**: when key people leave, knowledge disappears or becomes hard to locate.
- **Compliance risk**: policies and quality templates (e.g., ISO artifacts) must be current and traceable.

## 2. Users and Jobs-To-Be-Done

### 2.1 Primary users

- **Consultants**
  - Need quick answers about company methodologies, prior project approaches, templates, and policies.
  - Want citations to justify decisions to leads and clients.
- **Support/admin staff**
  - Handle repetitive internal requests (travel policy, templates, onboarding, compliance packs).
  - Need fast, consistent, defensible answers with links/citations.
- **Team leads / practice leads**
  - Verify whether a topic is covered, what the canonical policy is, and whether materials are up-to-date.

### 2.2 Secondary users

- **Knowledge manager / admin**
  - Maintains document corpus: add/remove docs, manage collections, mark superseded versions, trigger re-indexing.
  - Needs ingestion visibility, failure reporting, and safe operations (idempotent, resumable).

## 3. Current Manual Process (Baseline)

1. User has a question (e.g., “What’s our approach to supply chain risk assessment?”).
2. Search Slack history; maybe find an old thread.
3. Search Drive/SharePoint; browse inconsistent folder structures.
4. Open multiple documents; Ctrl+F in each.
5. If still blocked, ask senior colleague.
6. Senior colleague answers from memory or repeats the search.

**Observed baseline**:
- **Time per question**: 15–30 minutes.
- **Firm-wide frequency**: 20–40 times per day.

### 3.1 Why the manual process fails

- No unified search across formats and storage systems.
- Slack search finds messages, not authoritative document content.
- Folder structures vary by practice area; naming conventions are inconsistent.
- Document recency is unclear (latest vs outdated).
- Knowledge is person-dependent; staff churn causes permanent loss.

## 4. Problem Statement

The firm lacks a **single, trusted, queryable source of truth** for internal knowledge. As a result, internal questions incur high time cost, cause interruptions, and create quality/compliance risk due to outdated or inconsistent information.

The system must provide **fast, accurate, cited answers grounded in firm documents**, while enforcing **privacy and access control** and producing **operational telemetry** (cost, failures, retrieval quality) needed to run the service reliably.

## 5. Goals (What “Good” Looks Like)

### 5.1 Product goals

- **Answer internal questions quickly** via natural language chat.
- **Ground responses in retrieved document chunks only**, with citations that can be verified.
- **Support multi-turn follow-ups** while maintaining conversation context.
- **Refuse out-of-scope questions** rather than hallucinate.
- **Offer admin operations** to maintain corpus and manage versions/collections.
- **Provide analytics** for improvement (most asked, failed, cost, latency, retrieval quality).

### 5.2 Engineering goals

- Production-grade reliability: retries, circuit breaker, timeouts, idempotency, resumable ingestion.
- Security posture: access control, prompt injection mitigation, PII detection/redaction policies.
- Observability: structured logs, correlation IDs, audit trails, cost and latency metrics.

## 6. Non-Goals (Explicitly Out of Scope for This Project)

- **Authoritative policy changes**: the system surfaces content; it does not create or approve new policy.
- **Autonomous document editing**: no automatic rewriting or updating of source documents.
- **Cross-system permissions federation**: not building full SSO/IdP integration in initial version (can be stubbed with role-based access tokens).
- **Full enterprise search replacement**: goal is high-quality Q&A with citations, not general-purpose file search.
- **Training a custom foundation model**: we use hosted LLM APIs; no model training.

## 7. Document Corpus (Inputs)

### 7.1 Formats and volume

- **PDF** deliverables/reports: 200+ (1–100 pages; average ~10 pages).
- **DOCX** SOPs/templates/methodologies: 150+.
- **Markdown** internal wiki exports: ~100.
- **Notion** exports: ~50.
- **Total**: ~500–800 documents initially; design target: **2,000+**.

### 7.2 Content domains

- Methodologies and playbooks
- Compliance policies and quality frameworks
- HR policies and internal procedures
- Client onboarding procedures
- Technical standards and reference templates
- Industry research summaries

### 7.3 Metadata expectations (minimum viable)

For every document, the system should track:

- **Document ID** (stable)
- **Title / filename**
- **Source system** (Drive/SharePoint/Notion/wiki export/local)
- **Collection(s)** (practice area / team)
- **Versioning**: version label + timestamps + superseded pointer
- **Restriction level** (public-internal vs restricted)
- **Ingestion status** (pending / processing / failed / indexed)

## 8. System Capabilities (Must-Haves)

### 8.1 Ingestion and indexing

- Ingest PDFs, DOCX, Markdown, and Notion exports.
- Extract text + structural anchors where possible (page number, headings, sections).
- Chunk documents with overlap; store chunk provenance (doc_id, page/section, offsets).
- Generate embeddings; store in ChromaDB (or compatible vector store).
- Support **re-indexing** (full and per-document).
- Support **resumable ingestion** with progress tracking and per-document failure logs.

### 8.2 Query and answer generation

- Chat interface accepts natural language questions.
- Retrieve relevant chunks using **multiple strategies**:
  - similarity search
  - MMR
  - hybrid (dense + keyword/BM25-style when available)
- Optional query rewriting to improve retrieval for vague questions.
- Generate an answer **only from retrieved chunks**; include:
  - document name
  - page/section reference
  - relevance score (per citation)
- Support multi-turn conversation context (follow-ups, clarifications).
- If insufficient evidence: **refuse** with a helpful “what I searched” summary and suggested rephrase.

### 8.3 Admin operations

- Add/remove documents.
- Manage collections and restrictions.
- Mark documents as **superseded** and define “latest” per policy/template family.
- Trigger re-index; view ingestion status and failure details.

### 8.4 Analytics and monitoring

- Query logging: question, timestamp, user/group, collections searched, retrieval stats.
- Failure analytics: “no relevant chunks,” “restricted,” “PII blocked,” “provider error,” etc.
- Token usage and cost per query; enforce configurable **daily cost limit**.
- Monitoring endpoints: liveness, readiness, and operational metrics.

## 9. System Outputs (Deliverables)

- **Chat API** (FastAPI): query → answer with citations.
- **Chat UI** (Streamlit): internal demo and functional interface.
- **Admin API** (FastAPI): document + collection management; ingestion control.
- **Evaluation pipeline**: 50+ Q&A test set; accuracy and retrieval metrics.
- **Monitoring**: structured logs + correlation IDs + cost/latency metrics.
- **Audit/analytics store**: PostgreSQL (query logs, ingestion logs, evaluation runs).

## 10. Requirements (Production-Realistic)

### 10.1 Non-functional requirements

- **Latency**: typical query response within **5–10 seconds**.
- **Scale**:
  - initial: 500–800 documents
  - designed: 2,000+ documents and growing
- **Cost controls**:
  - track tokens and USD per request
  - configurable daily cap; system must reject/queue when cap exceeded
- **Reliability**:
  - retry with exponential backoff for transient provider failures
  - circuit breaker to stop hammering failing dependencies
  - graceful degradation and clear user-facing error messages
- **Observability**:
  - correlation ID on every request
  - structured logging for ingestion, retrieval, generation, refusals
  - metrics endpoint returns real values (not placeholders)
- **Maintainability**:
  - clear layering (routes → services → repositories/integrations)
  - dependency injection for testability
  - versioned prompts and reproducible evaluation results

### 10.2 Security and privacy requirements

- **PII detection**:
  - detect potential PII in user question and retrieved context
  - configurable policy: block LLM call, redact, or allow with warnings (default: block or redact)
  - never log raw sensitive content; log previews only where safe
- **Access control**:
  - user groups map to allowed collections
  - retrieval must enforce collection filters
  - restricted documents must never be used to answer unauthorized queries
- **Prompt injection mitigation**:
  - input scanning for known injection patterns
  - strict separation of system instructions and user content
  - answers constrained to retrieved evidence; refusal on conflict/low evidence
- **Document restrictions**:
  - support “restricted” flag and optionally “client-sensitive” tag
  - enforce at ingestion (visibility) and at query time (retrieval filters)

## 11. Key Failure Modes and Guardrails

1. **Hallucinated answer not present in documents**
   - Guardrail: “answer from retrieved chunks only,” evidence threshold, refusal when insufficient support.
2. **Irrelevant retrieval**
   - Guardrail: multiple retrieval strategies (similarity/MMR/hybrid), query rewriting, retrieval evaluation (precision@k/recall@k).
3. **Outdated document used**
   - Guardrail: versioning and “superseded” handling; prefer latest; expose “document version” in citations.
4. **Restricted content exposure**
   - Guardrail: collection-based access control; refusal with generic guidance.
5. **Prompt injection via user query**
   - Guardrail: pattern detection + refusal; do not allow user text to modify system behavior.
6. **LLM API outage or rate limiting**
   - Guardrail: retry with backoff, circuit breaker, graceful failure response; record incident in logs/metrics.
7. **Large ingestion fails midway**
   - Guardrail: resumable ingestion with checkpointing; per-document error logging; admin re-run for failed docs only.

## 12. Success Criteria (Measurable)

### 12.1 Quality metrics

- **Answer accuracy**: **>85%** on a 50+ Q&A evaluation test set.
- **Citation accuracy**: **>90%** (cited source actually contains the answer).
- **Zero hallucinated answers**: system must refuse rather than guess when evidence is insufficient.

### 12.2 Performance and operational metrics

- **Average response time**: **<10 seconds**.
- **Cost tracking**: cost per query computed and stored; daily budget enforced.
- **Admin autonomy**: admin can add/remove docs and re-index without developer intervention.
- **Local operability**: runs via `docker-compose` in **<5 minutes** from cold start.

## 13. Constraints and Assumptions

### 13.1 Constraints (hard requirements)

- Documents may contain client-sensitive material; privacy controls are mandatory.
- System must produce verifiable citations with page/section anchors where available.
- Must provide multi-turn chat while preserving safety (no context leakage across users).

### 13.2 Assumptions (design inputs)

- Documents can be exported into a local ingestion workspace (initially) even if the long-term source is Drive/SharePoint/Notion.
- Access control can start as role/group claims provided to the API (token-based) before full SSO integration.
- The evaluation set can be curated from real internal questions with sensitive content redacted.

## 14. Acceptance Criteria (Phase 0 Exit)

This project’s problem is considered well-defined when:

- The **users**, **primary workflows**, and **pain points** are unambiguous.
- Requirements include **functional**, **non-functional**, **security/privacy**, and **observability** constraints.
- Success is defined with **measurable targets** (accuracy, citations, latency, cost, operability).
- Major failure modes have corresponding **guardrails** and **operational responses**.
- System boundaries are clear: what is in scope now vs later.

## 15. Glossary

- **RAG**: Retrieval-Augmented Generation; LLM answers grounded in retrieved documents.
- **Chunk**: a segment of document text stored with provenance metadata for retrieval.
- **Citation**: reference to a specific document location (page/section) used as evidence.
- **MMR**: Maximal Marginal Relevance; retrieval approach balancing relevance and diversity.
- **Prompt injection**: attempts to override system instructions via user-provided text.

