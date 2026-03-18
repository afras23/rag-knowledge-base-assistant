# ADR 001: Vector Database Selection

## Status

Accepted

## Context

The system must store and query embeddings for 500–800 documents initially (scaling to 2,000+), support metadata filtering (collections, restriction flags, versioning), and be runnable locally via `docker-compose` in under 5 minutes for portfolio review.

We need a vector database that supports:

- fast similarity search and metadata filtering
- local development and reproducible demos
- a clear migration path to managed infrastructure if scale/availability requirements increase

## Options Considered

### Option A — ChromaDB

- **Pros**
  - Free and local-first; no API key required
  - Fast setup for portfolio demos (dev-friendly)
  - Integrates cleanly with Python/LangChain
  - Supports metadata storage alongside vectors (needed for citations and access control filters)
- **Cons**
  - Not a managed service by default; operational burden increases at scale
  - Availability/backup/replication become your responsibility in production deployments
  - Horizontal scaling options are more limited compared to managed vector services

### Option B — Pinecone

- **Pros**
  - Managed, production-grade scaling and availability
  - Operational concerns handled (replication, uptime, monitoring primitives)
  - Strong performance for large indexes and concurrent workloads
- **Cons**
  - Requires API keys and paid usage (portfolio friction)
  - Adds external dependency and vendor lock-in considerations
  - Harder to run fully offline / local-only

### Option C — Weaviate

- **Pros**
  - Strong feature set (hybrid search, schema modeling, filtering)
  - Can be self-hosted or managed (deployment flexibility)
- **Cons**
  - More operational complexity than Chroma for local-first portfolio
  - Additional infrastructure overhead for small initial corpus

### Option D — Qdrant

- **Pros**
  - Good performance and filtering; production-friendly
  - Self-hosted option works well with Docker
  - Clear operational story for running as a service
- **Cons**
  - Extra service to operate in local stack compared to embedded/local Chroma flows
  - Requires more setup/ops documentation for portfolio reviewers

### Option E — pgvector (PostgreSQL extension)

- **Pros**
  - Single datastore (PostgreSQL) simplifies operations and backups
  - Strong transactional guarantees and mature tooling
  - Great for moderate scale when vectors + metadata need tight coupling
- **Cons**
  - Vector search performance/scaling can be limiting for larger corpora and higher QPS
  - Index tuning and performance become specialized
  - Blends analytics/admin workloads with retrieval workload on one database

## Decision

Use **ChromaDB** as the vector store for development and portfolio delivery, with an explicit **migration path to Pinecone** for production scale deployments.

- ChromaDB provides the fastest path to a local, reproducible demo without external credentials.
- The architecture will isolate vector store operations behind a repository/client interface so the backend can be switched to Pinecone with minimal changes.

## Consequences

- **Positive**
  - Local-first stack is easy to run and review (CTO-friendly portfolio signal).
  - Minimal operational overhead during early development.
  - Clear abstraction boundary allows swapping vector DB providers.
- **Negative**
  - If deployed for production-like workloads, we must add operational controls for Chroma (backup/restore, persistence, availability).
  - Migration requires re-indexing embeddings and validating retrieval quality parity.
- **Follow-on requirements**
  - Enforce strict metadata schema so filters work identically across providers (collections, restrictions, doc_version).
  - Maintain a re-index pipeline that can rebuild the vector index to support provider migration.

