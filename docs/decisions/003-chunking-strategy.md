# ADR 003: Chunking Strategy

## Status

Accepted

## Context

Documents must be split into chunks prior to embedding. Chunk size and overlap directly affect:

- retrieval recall (can we find the right passage?)
- precision (do retrieved chunks contain enough context to answer?)
- citation usefulness (page/section anchors remain meaningful)
- cost and latency (more chunks increase embedding and retrieval overhead)

The corpus includes PDFs (often with layout artifacts), DOCX methodologies/templates, Markdown wiki pages, and Notion exports. The chunking strategy must handle all formats consistently while preserving structure where available.

## Options Considered

### Option A — Fixed-size token chunks (e.g., 512 / 1024 tokens)

- **Pros**
  - Predictable chunk size relative to LLM context windows
  - Common approach; easy to reason about token budgets
- **Cons**
  - Tokenization varies by model/provider; harder to keep consistent across components
  - Can split in the middle of headings/tables/numbered procedures
  - Requires model-specific tooling and careful performance tuning

### Option B — Recursive character splitting (size + overlap)

- **Pros**
  - Robust across formats (works on extracted plain text)
  - Doesn’t require model-specific tokenization during ingestion
  - Good balance of simplicity and quality for heterogeneous corpora
- **Cons**
  - Character count is an approximation of “semantic size”
  - Without structure awareness, can still cut across section boundaries

### Option C — Semantic chunking

- **Pros**
  - Chunks align with meaning boundaries (better coherence)
  - Often improves retrieval precision in narrative documents
- **Cons**
  - More expensive and complex (often requires additional models)
  - Harder to debug and reproduce; can introduce nondeterminism
  - Overkill for templates/policies where structure is explicit

### Option D — Document-structure-aware chunking (headings/sections first)

- **Pros**
  - Preserves section context, especially for SOPs and methodologies
  - Improves citation quality (sections map cleanly to user-visible headings)
  - Works well for Markdown and DOCX where headings are explicit
- **Cons**
  - Requires format-specific parsing and normalization
  - Less effective for PDFs with poor structural extraction

## Decision

Use **recursive character splitting** with:

- **chunk size**: **1000 characters**
- **overlap**: **200 characters**

Augment with **document-structure-aware splitting** for Markdown and DOCX:

- split by headers/heading styles first (section-aware),
- then apply recursive splitting within each section to enforce maximum chunk size.

Chunk size and overlap are **configurable**, documented, and tuneable based on evaluation outcomes.

## Consequences

- **Positive**
  - Works consistently across all supported formats.
  - Header-aware chunking preserves section context where available (better retrieval + citations).
  - 200-character overlap reduces boundary-loss for procedures and definitions spanning splits.
- **Negative**
  - Some PDFs will still yield imperfect boundaries due to extraction artifacts.
  - Chunk size in characters is only an approximation; token budgets must be handled at query-time.
- **Operational requirements**
  - Store chunk provenance metadata (doc_id, page/section/heading, chunk_index, offsets).
  - Track chunking configuration version in ingestion logs so retrieval regressions can be correlated to chunking changes.
  - Re-index required when chunking strategy parameters change.

