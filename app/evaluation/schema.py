"""
Pydantic models for evaluation test cases and JSON reports.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator

EvalCategory = Literal[
    "standard",
    "multi_turn",
    "out_of_scope",
    "restricted",
    "injection",
    "ambiguous",
]


class EvalTestCase(BaseModel):
    """One row from eval/test_set.jsonl."""

    question: str = Field(..., min_length=1)
    expected_answer_contains: list[str] = Field(default_factory=list)
    expected_source_doc: str | None = Field(
        default=None,
        description="Filename under eval/sample_docs/ or null when no gold document applies.",
    )
    expected_section: str = Field(default="", description="Section or heading anchor for human review.")
    category: EvalCategory
    collection: str = Field(default="default")
    follow_up_to: str | None = Field(default=None, description="case_id this question follows.")
    case_id: str | None = Field(default=None, description="Stable id for multi-turn linking.")
    user_group: str | None = Field(
        default=None,
        description="Simulated access group; None excludes confidential chunks in offline retrieval.",
    )

    @field_validator("expected_source_doc", mode="before")
    @classmethod
    def _empty_string_to_none(cls, value: object) -> str | None:
        if value is None or value == "":
            return None
        if isinstance(value, str):
            return value
        raise TypeError("expected_source_doc must be str or null")


class EvalCaseResult(BaseModel):
    """Per-case metrics and flags."""

    case_id: str | None = None
    category: EvalCategory
    precision_at_k: float | None = None
    recall_at_k: float | None = None
    retrieval_skipped: bool = False
    guardrail_blocked: bool | None = None
    guardrail_expected: bool | None = None
    confidential_excluded: bool | None = None
    out_of_scope_low_evidence: bool | None = None
    citation_accuracy: float | None = None
    groundedness: float | None = None
    correctness: float | None = None
    completeness: float | None = None
    answer_contains_hits: int | None = None
    notes: list[str] = Field(default_factory=list)


class EvalAggregateMetrics(BaseModel):
    """Roll-up metrics across the test set."""

    k: int = Field(..., ge=1)
    mean_precision_at_k: float = Field(..., ge=0.0, le=1.0)
    mean_recall_at_k: float = Field(..., ge=0.0, le=1.0)
    cases_scored_retrieval: int = Field(..., ge=0)
    mean_citation_accuracy: float | None = Field(default=None, ge=0.0, le=1.0)
    cases_with_citations: int = Field(default=0, ge=0)
    mean_groundedness: float | None = None
    mean_correctness: float | None = None
    mean_completeness: float | None = None
    injection_block_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    out_of_scope_low_evidence_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    restricted_exclusion_rate: float | None = Field(default=None, ge=0.0, le=1.0)


class EvalReport(BaseModel):
    """Written to eval/results/eval_YYYY-MM-DD.json."""

    run_id: str = Field(..., description="UTC ISO timestamp for this run.")
    test_set_path: str
    sample_docs_manifest: str
    k: int
    llm_judge_enabled: bool
    metrics: EvalAggregateMetrics
    per_case: list[EvalCaseResult]
    notes: list[str] = Field(default_factory=list)

    @staticmethod
    def default_run_id() -> str:
        """UTC ISO-8601 run identifier."""
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
