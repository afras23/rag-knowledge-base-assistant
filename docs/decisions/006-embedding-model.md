# ADR 006: Embedding Model Selection

## Status

Accepted

## Context

The system requires an embedding model for:

- indexing 500–800 documents initially (scaling to 2,000+)
- supporting retrieval across heterogeneous formats (PDF/DOCX/Markdown/Notion)
- enabling metadata-filtered retrieval (collections/restrictions/versioning)
- operating within a strict latency target (5–10 seconds end-to-end per query)
- running locally via `docker-compose` for portfolio review

Embedding choice materially impacts retrieval quality (and therefore citation accuracy). It also affects operational constraints: cost per document ingested, query latency (if embedding queries remotely), and reliability (external dependency vs local inference).

## Options Considered

### Option A — OpenAI embeddings (hosted)

- **Pros**
  - High quality embeddings for broad enterprise text
  - Simple API integration; no GPU requirements
  - Predictable performance and good tooling ecosystem
- **Cons**
  - Ongoing cost for ingestion (every chunk) and (potentially) query-time embedding
  - External dependency (rate limits/outages), requires API key
  - Privacy considerations: text leaves the environment (must enforce PII policy before calls)

### Option B — Open-source embeddings (local), e.g. BGE / E5

- **Pros**
  - Local inference possible (no external API calls, strong privacy posture)
  - Lower marginal cost once running (especially for re-indexing)
  - Removes vendor dependency for retrieval layer
- **Cons**
  - Operational complexity: packaging model weights, CPU/GPU performance tuning
  - Latency can be higher on CPU-only machines for large ingestion batches
  - Quality may lag behind top hosted models depending on chosen checkpoint and domain

### Option C — Hybrid: local embeddings for dev + hosted for production

- **Pros**
  - Local-first demo experience with a clear “upgrade path”
  - Ability to compare retrieval quality and cost side-by-side
- **Cons**
  - Two embedding backends to support and evaluate
  - Requires disciplined configuration and reproducible evaluation to avoid confusion

## Decision

Default to **open-source local embeddings** for the portfolio/local stack using **`bge-small`** (CPU-friendly), with a configuration switch to use **OpenAI embeddings** for deployments where higher quality and managed performance are required.

This aligns with project requirements:

- local-first demo with no required API key
- strong privacy posture (no text leaves the environment for embeddings by default)
- explicit migration path via a provider abstraction and re-index pipeline

## Consequences

- **Positive**
  - Local ingestion works offline and is cost-stable (no per-chunk embedding bill).
  - Reduced exposure of sensitive text to external services by default.
  - Supports portfolio reviewers who want to run everything locally.
- **Negative**
  - Local embedding throughput may be slower on CPU-only machines; ingestion may take longer for large batches.
  - Retrieval quality must be validated; some domains may benefit from hosted embeddings.
  - Switching embedding models requires **full re-indexing** and retrieval metric comparison.
- **Operational requirements**
  - Treat embedding backend as a pluggable dependency (provider abstraction).
  - Persist the embedding model name/version used for each index build in PostgreSQL ingestion metadata.
  - Maintain evaluation metrics (precision@k/recall@k) to justify future upgrades (e.g., OpenAI embeddings).

