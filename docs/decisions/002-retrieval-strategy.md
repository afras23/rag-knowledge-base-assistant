# ADR 002: Retrieval Strategy

## Status

Accepted

## Context

The system must retrieve the most relevant document chunks for a user question while:

- avoiding redundant evidence (near-duplicate chunks from the same section)
- supporting ambiguous or underspecified questions (common in internal consulting queries)
- enabling verifiable citations (retrieval quality directly impacts citation accuracy)
- operating under latency constraints (5–10 seconds end-to-end)

Retrieval is a primary determinant of answer quality in a RAG system, so we need a strategy that balances relevance, diversity, and robustness across topics and document types.

## Options Considered

### Option A — Simple similarity search (dense only)

- **Pros**
  - Simple to implement and tune
  - Lowest latency and operational complexity
- **Cons**
  - Often returns redundant chunks (same section rephrased or overlapping)
  - Struggles with keyword-heavy queries (policy numbers, template names, acronyms)
  - Less robust when the user question is broad or ambiguous

### Option B — MMR (Maximum Marginal Relevance)

- **Pros**
  - Improves evidence diversity by penalizing near-duplicate chunks
  - Often increases coverage for multi-part questions
  - Works well with chunk overlap where redundancy is otherwise amplified
- **Cons**
  - Adds tuning complexity (lambda/diversity parameter)
  - Can surface slightly less relevant chunks if diversity weight is too high

### Option C — Hybrid retrieval (keyword + semantic)

- **Pros**
  - More robust to exact terms (policy names, doc titles, acronyms, IDs)
  - Helps when embeddings miss rare tokens or formatting artifacts
  - Often improves recall on heterogeneous corpora
- **Cons**
  - More components to operate (keyword index or compatible hybrid implementation)
  - Requires careful normalization to avoid noisy matches (e.g., boilerplate)

### Option D — Query rewriting + retrieval

- **Pros**
  - Improves retrieval for ambiguous questions (expands acronyms, adds context)
  - Enables decomposition into sub-queries for complex questions
  - Can significantly improve recall without changing storage layer
- **Cons**
  - Introduces LLM dependency before retrieval (cost + failure mode)
  - Requires guardrails so rewritten query does not drift from user intent

## Decision

Use a **hybrid approach**:

- **MMR** as the default retrieval strategy (dense retrieval with diversity).
- **Query rewriting** for complex or ambiguous questions (configurable and traceable).
- Hybrid keyword + semantic retrieval is used when available/configured to improve recall on keyword-heavy queries.

## Consequences

- **Positive**
  - Reduces redundant evidence and improves coverage for multi-part questions.
  - Better handles internal corpora with templates, IDs, acronyms, and policy names.
  - Improves citation quality by retrieving a more representative evidence set.
- **Negative**
  - More tuning surface area (MMR parameters, rewrite triggers, retrieval fusion weights).
  - Additional latency/cost when rewriting is invoked.
  - Requires rigorous observability and evaluation to detect regressions.
- **Operational requirements**
  - Log retrieval strategy used per query (similarity/MMR/hybrid, rewrite on/off) for analytics.
  - Evaluate retrieval separately (precision@k/recall@k) to avoid conflating retrieval vs generation failures.

