# ADR 005: Evaluation Methodology

## Status

Accepted

## Context

The project must demonstrate measurable system quality for a senior reviewer and provide a framework for continuous improvement. RAG systems fail in multiple independent ways:

- retrieval misses the right evidence (recall failure)
- retrieved chunks are irrelevant or redundant (precision failure)
- generation misuses evidence (answer quality failure)
- citations are incorrect even if the answer sounds right (citation accuracy failure)

We need an evaluation approach that measures each failure mode separately and supports regression detection when prompts, chunking, embeddings, or retrieval strategies change.

## Options Considered

### Option A — Manual testing only

- **Pros**
  - Fast for early iteration and qualitative assessment
  - Good for discovering UX issues and unexpected queries
- **Cons**
  - Not reproducible; doesn’t prevent regressions
  - Hard to quantify progress and compare configurations
  - Not credible as the only evaluation method for a production-grade portfolio

### Option B — Automated exact-match scoring (answer text)

- **Pros**
  - Easy to compute and trend over time
  - Deterministic when outputs are deterministic
- **Cons**
  - Not suitable for natural language answers with multiple valid phrasings
  - Can penalize correct answers that use different wording
  - Doesn’t capture citation correctness unless modeled explicitly

### Option C — LLM-as-judge for answer quality

- **Pros**
  - Can evaluate semantic correctness, completeness, and helpfulness
  - Can score rubric-based criteria (e.g., “uses only provided evidence”)
  - Useful for qualitative quality signals at scale
- **Cons**
  - Adds cost and introduces evaluator drift/bias
  - Requires careful rubric design and calibration
  - Must be used alongside objective metrics to remain credible

### Option D — Retrieval metrics (precision@k, recall@k)

- **Pros**
  - Directly measures retrieval quality independent of generation
  - Enables tuning of chunking, embeddings, MMR/hybrid parameters
  - Strong debugging signal: “did we retrieve the right evidence?”
- **Cons**
  - Requires labeled ground-truth relevant documents/chunks per question
  - Doesn’t measure answer quality or citation formatting correctness

## Decision

Use a **combined evaluation methodology**:

1. **Retrieval quality**: precision@k and recall@k (and optionally MRR/nDCG) against a labeled evaluation set.
2. **Answer quality**: LLM-as-judge with a strict rubric (correctness, completeness, groundedness).
3. **Citation accuracy**: exact citation matching where feasible (document + page/section) plus automated verification that the cited chunk contains the supporting text.

## Consequences

- **Positive**
  - Measures independent failure modes: retrieval, generation, and citations.
  - Enables targeted improvements (e.g., fix retrieval without changing prompts).
  - Produces credible, reportable metrics in a portfolio setting.
- **Negative**
  - Requires evaluation data curation and ongoing maintenance.
  - LLM-as-judge introduces additional cost and requires stable rubrics.
  - Citation verification may require format-specific anchoring logic (PDF pages vs Markdown headings).
- **Operational requirements**
  - Store evaluation runs and configs (model, prompt version, chunking config, retrieval strategy).
  - Run evaluation in CI for regressions on a small “smoke” subset; run full evaluation on demand.
  - Track metrics over time to detect drift after ingestion or configuration changes.

