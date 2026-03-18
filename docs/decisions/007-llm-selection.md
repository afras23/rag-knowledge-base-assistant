# ADR 007: LLM Selection (Answer Generation + Query Rewriting + Reranking)

## Status

Accepted

## Context

The system uses an LLM for:

- **answer generation** grounded in retrieved chunks (must avoid hallucination)
- **query rewriting** for ambiguous/complex questions (optional, controlled)
- **reranking** (optional, controlled) when non-LLM rerankers are unavailable

The LLM choice affects:

- response latency (must typically complete within 5–10 seconds)
- cost per query (tracked and capped daily)
- reliability (rate limits/outages)
- context window (must fit question + retrieved evidence + instructions)
- citation behavior (model must follow “use only provided sources” constraints)

## Options Considered

### Option A — OpenAI GPT-4o / GPT-4.1 (hosted)

- **Pros**
  - Strong instruction-following and stable performance for grounded Q&A
  - Large context windows suitable for multi-chunk evidence prompts
  - High reliability for production-style workloads compared to most local setups
  - Good tooling ecosystem and predictable integration
- **Cons**
  - External dependency (network, rate limits, outages)
  - Paid usage; must enforce cost controls
  - Sensitive text leaves the environment (requires PII detection/redaction policy)

### Option B — Local/self-hosted models (e.g., Llama-family, Mistral-family)

- **Pros**
  - Full data control; can keep all text on-prem/local
  - Potentially lower marginal cost at scale if hardware is available
  - Avoids vendor lock-in for generation
- **Cons**
  - Operational burden (serving, scaling, monitoring, GPU requirement for good latency)
  - Lower reliability/quality variance depending on model and prompting
  - Citation fidelity and groundedness often require heavier prompt engineering and guardrails

### Option C — Hybrid: hosted LLM for generation + local fallback for outages

- **Pros**
  - Better resilience story; can degrade to local model for limited responses
  - Can reserve hosted usage for higher-value queries
- **Cons**
  - Complex behavior differences; must evaluate both paths
  - Risk of inconsistent outputs and hard-to-debug regressions

## Decision

Use **OpenAI** as the default LLM provider for this system, with:

- **GPT-4o** as the primary model for answer generation (balanced latency/cost/quality),
- **GPT-4.1** as an optional alternative for higher accuracy needs (configurable),
- a **local model option** reserved for future “privacy-first/on-prem” deployments, but not the default for this portfolio stack.

This decision prioritizes:

- reliable instruction-following for grounded answers with citations
- adequate context window to include evidence without truncation
- production-realistic reliability characteristics and developer ergonomics

## Consequences

- **Positive**
  - Strong answer quality and grounded behavior for RAG with citations.
  - Simpler system operations than running local GPU inference.
  - Predictable latency profile suitable for interactive chat.
- **Negative**
  - Requires rigorous cost tracking and daily budget enforcement.
  - External dependency: must implement retry/backoff, circuit breaker, and graceful error responses.
  - Privacy controls become mandatory: PII detection/redaction before any LLM call and strict logging hygiene.
- **Operational requirements**
  - Persist model name/version, prompt version, and per-request token/cost metadata in PostgreSQL.
  - Enforce retrieval-grounded generation and refuse when evidence is insufficient.
  - Maintain a configuration-based provider abstraction to support future local model integration without refactoring the service layer.

