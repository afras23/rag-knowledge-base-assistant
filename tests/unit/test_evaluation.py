"""
Unit tests for evaluation metrics, citations, and report schema (Phase 10).
"""

from __future__ import annotations

from uuid import UUID

from app.api.schemas.chat import CitationSchema
from app.evaluation.citations import citation_accuracy_ratio
from app.evaluation.metrics import precision_at_k, recall_at_k
from app.evaluation.schema import EvalAggregateMetrics, EvalCaseResult, EvalReport
from app.services.vectorstore.chroma_client import RetrievedChunk


def test_precision_at_k() -> None:
    relevant = {"a", "b"}
    retrieved = ["x", "a", "y", "b", "z"]
    assert precision_at_k(relevant, retrieved, k=4) == 0.5
    assert precision_at_k(relevant, retrieved, k=2) == 0.5
    assert precision_at_k(set(), retrieved, k=3) == 0.0


def test_recall_at_k() -> None:
    relevant = {"a", "b"}
    retrieved = ["x", "a", "b"]
    assert recall_at_k(relevant, retrieved, k=3) == 1.0
    assert recall_at_k(relevant, retrieved, k=1) == 0.0
    assert recall_at_k(set(), retrieved, k=5) == 1.0


def test_citation_accuracy() -> None:
    did = "a3d7f2c1-4e9b-5d8a-9c2e-1f4b6a8d0e12"
    chunk = RetrievedChunk(
        text="The organization shall retain financial records for seven years.",
        doc_id=did,
        document_title="Policy",
        page_or_section="Retention",
        relevance_score=0.9,
        collection_id="default",
        restriction_level="public",
        chunk_index=0,
    )
    good = CitationSchema(
        document_title="Policy",
        doc_id=UUID(did),
        page_or_section="Retention",
        relevance_score=0.9,
        chunk_preview="seven years",
    )
    ratio, issues = citation_accuracy_ratio([good], [chunk])
    assert ratio == 1.0
    assert issues == []

    bad = CitationSchema(
        document_title="Policy",
        doc_id=UUID(did),
        page_or_section="Retention",
        relevance_score=0.9,
        chunk_preview="this text is not in the chunk body",
    )
    ratio_bad, issues_bad = citation_accuracy_ratio([bad], [chunk])
    assert ratio_bad == 0.0
    assert issues_bad


def test_report_schema() -> None:
    metrics = EvalAggregateMetrics(
        k=5,
        mean_precision_at_k=0.4,
        mean_recall_at_k=0.6,
        cases_scored_retrieval=10,
    )
    report = EvalReport(
        run_id="2026-01-01T00:00:00Z",
        test_set_path="eval/test_set.jsonl",
        sample_docs_manifest="eval/sample_docs/manifest.json",
        k=5,
        llm_judge_enabled=False,
        metrics=metrics,
        per_case=[
            EvalCaseResult(
                case_id="s-01",
                category="standard",
                precision_at_k=1.0,
                recall_at_k=1.0,
            ),
        ],
    )
    dumped = report.model_dump()
    round_trip = EvalReport.model_validate(dumped)
    assert round_trip.metrics.k == 5
    assert round_trip.per_case[0].case_id == "s-01"


def test_citation_missing_doc_id() -> None:
    cite = CitationSchema(
        document_title="X",
        doc_id=UUID("00000000-0000-4000-8000-000000000001"),
        page_or_section="1",
        relevance_score=0.5,
        chunk_preview="preview",
    )
    ratio, issues = citation_accuracy_ratio([cite], [])
    assert ratio == 0.0
    assert any("doc_id_not_in_chunks" in issue for issue in issues)
