# ADR 004: Guardrails Approach (Hallucination, Out-of-Scope, Prompt Injection)

## Status

Accepted

## Context

The system must:

- **avoid hallucinations** (never invent facts not present in the corpus)
- **refuse** out-of-scope questions when the corpus lacks relevant evidence
- **block prompt injection** attempts in user input and prevent instruction override
- operate under latency and cost constraints

Guardrails must be reliable, auditable, and measurable through evaluation metrics (accuracy, refusal correctness, citation accuracy).

## Options Considered

### Option A — Post-generation fact-checking

- **Pros**
  - Can catch some hallucinations after the model produces an answer
  - Potentially adds an extra layer of validation for high-risk outputs
- **Cons**
  - Expensive (additional LLM calls and/or retrieval passes)
  - Unreliable: models can “confirm” their own hallucinations
  - Adds latency and complexity; increases failure surface area

### Option B — Retrieval-grounded-only generation

- **Pros**
  - Directly constrains the model to provided evidence (best first-line defense)
  - Improves citation accuracy because sources are explicit inputs
  - Predictable behavior: no retrieval → no answer
- **Cons**
  - Requires careful evidence selection and prompt construction
  - If retrieval fails, the system must refuse even if the answer exists somewhere in the corpus

### Option C — Confidence thresholding (evidence relevance thresholds)

- **Pros**
  - Prevents low-support answers when retrieval confidence is low
  - Explicit and measurable: relevance thresholds can be tuned
  - Reduces risk of “confident nonsense”
- **Cons**
  - Requires tuning and evaluation to avoid excessive refusals
  - Thresholds may need adjustment per collection/document type

## Decision

Implement guardrails as **retrieval-grounded generation** with:

- **minimum relevance / evidence sufficiency threshold** (refuse if not met)
- **prompt injection pattern matching** on user inputs (block/refuse)
- **refusal when no relevant chunks are found** (no best-guess answers)

This guardrail stack is enforced before and during generation:

- no LLM call for answer generation without retrieved evidence
- system prompt explicitly forbids using knowledge outside provided chunks

## Consequences

- **Positive**
  - Strongest practical reduction in hallucinations for RAG systems.
  - Clear refusal behavior is safer than speculative answers.
  - Measurable tuning knobs (relevance thresholds, retrieval parameters).
- **Negative**
  - Retrieval quality becomes a gating dependency; poor retrieval increases refusal rate.
  - Requires careful UX for refusals (“what I searched” + suggested rephrase).
- **Operational requirements**
  - Log guardrail outcomes (refusal reasons, injection detections, threshold values).
  - Maintain an evaluation set that includes out-of-scope questions and injection attempts.
  - Monitor refusal rate and correlate with retrieval metrics to avoid over-blocking.

