"""
Evaluation orchestration (offline retrieval, guardrails, optional LLM judge).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from app.ai.guardrails import GuardrailService
from app.ai.llm_client import LlmClient
from app.api.schemas.chat import CitationSchema
from app.config import settings
from app.evaluation.citations import citation_accuracy_ratio
from app.evaluation.judge import score_answer_with_llm_judge
from app.evaluation.metrics import precision_at_k, recall_at_k
from app.evaluation.offline_retrieval import (
    build_chunks_from_sample_docs,
    load_manifest,
    rank_chunks_for_question,
)
from app.evaluation.schema import (
    EvalAggregateMetrics,
    EvalCaseResult,
    EvalReport,
    EvalTestCase,
)
from app.services.vectorstore.chroma_client import RetrievedChunk

logger = logging.getLogger(__name__)

LOW_EVIDENCE_THRESHOLD = 0.05
RETRIEVAL_SCORE_CATEGORIES = frozenset({"standard", "multi_turn", "ambiguous"})


@dataclass
class _RollupState:
    """Mutable accumulators while iterating test cases."""

    precisions: list[float] = field(default_factory=list)
    recalls: list[float] = field(default_factory=list)
    citation_scores: list[float] = field(default_factory=list)
    grounded: list[float] = field(default_factory=list)
    correctness: list[float] = field(default_factory=list)
    completeness: list[float] = field(default_factory=list)
    inj_ok: int = 0
    inj_total: int = 0
    oos_ok: int = 0
    oos_total: int = 0
    res_ok: int = 0
    res_total: int = 0


@dataclass(frozen=True)
class EvaluationPaths:
    """Filesystem locations for an evaluation run."""

    repo_root: Path
    test_set: Path
    manifest: Path
    sample_dir: Path
    results_dir: Path


def load_test_cases(path: Path) -> list[EvalTestCase]:
    """
    Load newline-delimited JSON test cases.

    Args:
        path: Path to ``test_set.jsonl``.

    Returns:
        Parsed test cases in file order.

    Raises:
        FileNotFoundError: When the path does not exist.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    cases: list[EvalTestCase] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        payload = json.loads(stripped)
        cases.append(EvalTestCase.model_validate(payload))
    return cases


def case_index_by_id(cases: list[EvalTestCase]) -> dict[str, EvalTestCase]:
    """Map non-null ``case_id`` values to test cases."""
    return {c.case_id: c for c in cases if c.case_id is not None}


def effective_question_text(case: EvalTestCase, by_id: dict[str, EvalTestCase]) -> str:
    """
    Merge follow-up questions with their ancestors for multi-turn overlap scoring.

    Args:
        case: Current test case.
        by_id: Lookup for prior cases.

    Returns:
        Single string used for offline retrieval simulation.
    """
    parts: list[str] = [case.question]
    parent_id = case.follow_up_to
    seen: set[str] = set()
    while parent_id and parent_id not in seen:
        seen.add(parent_id)
        parent = by_id.get(parent_id)
        if parent is None:
            break
        parts.insert(0, parent.question)
        parent_id = parent.follow_up_to
    return " ".join(parts)


def doc_id_for_filename(manifest: dict[str, Any], filename: str) -> str | None:
    """Resolve manifest filename to ``doc_id`` string."""
    docs = manifest.get("docs", {})
    meta = docs.get(filename)
    if not meta:
        return None
    return str(meta.get("doc_id"))


def build_stub_answer(chunks: list[RetrievedChunk], phrases: list[str]) -> str:
    """Create a deterministic pseudo-answer for optional LLM judging."""
    head = chunks[0].text[:600] if chunks else "No on-topic evidence was retrieved."
    tail = ", ".join(phrases) if phrases else ""
    return f"Based on the evidence:\n{head}\n\nHighlighted phrases: {tail}"


def stub_citations_from_chunks(chunks: list[RetrievedChunk]) -> list[CitationSchema]:
    """Build citations aligned with the top chunk for citation metric checks."""
    if not chunks:
        return []
    top = chunks[0]
    preview = top.text[:180].replace("\n", " ")
    return [
        CitationSchema(
            document_title=top.document_title,
            doc_id=UUID(top.doc_id),
            page_or_section=top.page_or_section,
            relevance_score=min(1.0, max(0.0, top.relevance_score)),
            chunk_preview=preview,
        ),
    ]


def _float_or_none(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _rollup_case_metrics(case: EvalTestCase, result: EvalCaseResult, state: _RollupState) -> None:
    """Merge one case outcome into aggregate accumulators."""
    if case.category == "injection":
        state.inj_total += 1
        if result.guardrail_expected and result.guardrail_blocked:
            state.inj_ok += 1
    elif case.category == "out_of_scope":
        state.oos_total += 1
        if result.out_of_scope_low_evidence:
            state.oos_ok += 1
    elif case.category == "restricted":
        state.res_total += 1
        if result.confidential_excluded:
            state.res_ok += 1

    if result.precision_at_k is not None and result.recall_at_k is not None:
        state.precisions.append(result.precision_at_k)
        state.recalls.append(result.recall_at_k)
    if result.citation_accuracy is not None:
        state.citation_scores.append(result.citation_accuracy)
    if result.groundedness is not None:
        state.grounded.append(result.groundedness)
    if result.correctness is not None:
        state.correctness.append(result.correctness)
    if result.completeness is not None:
        state.completeness.append(result.completeness)


def _build_aggregate_metrics(state: _RollupState, k: int) -> EvalAggregateMetrics:
    """Build aggregate metrics from rollup state."""
    mean_p = sum(state.precisions) / len(state.precisions) if state.precisions else 0.0
    mean_r = sum(state.recalls) / len(state.recalls) if state.recalls else 0.0
    return EvalAggregateMetrics(
        k=k,
        mean_precision_at_k=mean_p,
        mean_recall_at_k=mean_r,
        cases_scored_retrieval=len(state.precisions),
        mean_citation_accuracy=(
            sum(state.citation_scores) / len(state.citation_scores) if state.citation_scores else None
        ),
        cases_with_citations=len(state.citation_scores),
        mean_groundedness=sum(state.grounded) / len(state.grounded) if state.grounded else None,
        mean_correctness=sum(state.correctness) / len(state.correctness) if state.correctness else None,
        mean_completeness=sum(state.completeness) / len(state.completeness) if state.completeness else None,
        injection_block_rate=(state.inj_ok / state.inj_total) if state.inj_total else None,
        out_of_scope_low_evidence_rate=(state.oos_ok / state.oos_total) if state.oos_total else None,
        restricted_exclusion_rate=(state.res_ok / state.res_total) if state.res_total else None,
    )


async def run_evaluation(
    *,
    paths: EvaluationPaths,
    k: int,
    max_chunks: int,
    with_llm: bool,
) -> EvalReport:
    """
    Execute the evaluation pipeline and return a structured report.

    Args:
        paths: Resolved repository paths.
        k: Rank cut-off for precision/recall.
        max_chunks: Offline retrieval list size.
        with_llm: When True and API key is set, run LLM-as-judge on stub answers.

    Returns:
        ``EvalReport`` ready for JSON serialization.
    """
    manifest = load_manifest(paths.manifest)
    cases = load_test_cases(paths.test_set)
    chunks = build_chunks_from_sample_docs(sample_dir=paths.sample_dir, manifest=manifest)
    by_id = case_index_by_id(cases)
    filename_to_id = {fn: str(meta["doc_id"]) for fn, meta in manifest["docs"].items()}

    guard = GuardrailService()
    llm = LlmClient(settings=settings) if with_llm else None
    llm_judge_enabled = bool(with_llm and settings.openai_api_key)

    per_case: list[EvalCaseResult] = []
    rollup = _RollupState()

    for case in cases:
        result = await _evaluate_single_case(
            case=case,
            by_id=by_id,
            chunks=chunks,
            manifest=manifest,
            filename_to_id=filename_to_id,
            k=k,
            max_chunks=max_chunks,
            guard=guard,
            llm=llm,
            llm_judge_enabled=llm_judge_enabled,
        )
        per_case.append(result)
        _rollup_case_metrics(case, result, rollup)

    metrics = _build_aggregate_metrics(rollup, k)

    notes: list[str] = []
    if not llm_judge_enabled:
        notes.append("LLM judge disabled or missing OPENAI_API_KEY; judge scores omitted.")

    return EvalReport(
        run_id=EvalReport.default_run_id(),
        test_set_path=str(paths.test_set.relative_to(paths.repo_root)),
        sample_docs_manifest=str(paths.manifest.relative_to(paths.repo_root)),
        k=k,
        llm_judge_enabled=llm_judge_enabled,
        metrics=metrics,
        per_case=per_case,
        notes=notes,
    )


async def _evaluate_single_case(
    *,
    case: EvalTestCase,
    by_id: dict[str, EvalTestCase],
    chunks: list[RetrievedChunk],
    manifest: dict[str, Any],
    filename_to_id: dict[str, str],
    k: int,
    max_chunks: int,
    guard: GuardrailService,
    llm: LlmClient | None,
    llm_judge_enabled: bool,
) -> EvalCaseResult:
    """Dispatch evaluation logic by category."""
    if case.category == "injection":
        return await _eval_injection(case, guard)
    if case.category == "out_of_scope":
        return _eval_out_of_scope(case, by_id, chunks, max_chunks, case.user_group)
    if case.category == "restricted":
        return _eval_restricted(case, by_id, chunks, max_chunks, filename_to_id)
    return await _eval_retrieval_categories(
        case,
        by_id,
        chunks,
        manifest,
        k,
        max_chunks,
        llm,
        llm_judge_enabled,
    )


async def _eval_injection(case: EvalTestCase, guard: GuardrailService) -> EvalCaseResult:
    outcome = await guard.check_input(case.question)
    blocked = not outcome.is_safe
    return EvalCaseResult(
        case_id=case.case_id,
        category=case.category,
        retrieval_skipped=True,
        guardrail_blocked=blocked,
        guardrail_expected=True,
        notes=[] if blocked else ["expected_block_injection"],
    )


def _eval_out_of_scope(
    case: EvalTestCase,
    by_id: dict[str, EvalTestCase],
    chunks: list[RetrievedChunk],
    max_chunks: int,
    user_group: str | None,
) -> EvalCaseResult:
    q = effective_question_text(case, by_id)
    ranked = rank_chunks_for_question(
        question=q,
        chunks=chunks,
        user_group=user_group,
        max_chunks=max_chunks,
    )
    top = ranked[0].relevance_score if ranked else 0.0
    low = top < LOW_EVIDENCE_THRESHOLD
    return EvalCaseResult(
        case_id=case.case_id,
        category=case.category,
        retrieval_skipped=True,
        out_of_scope_low_evidence=low,
        notes=[] if low else ["unexpected_overlap_with_corpus"],
    )


def _eval_restricted(
    case: EvalTestCase,
    by_id: dict[str, EvalTestCase],
    chunks: list[RetrievedChunk],
    max_chunks: int,
    filename_to_id: dict[str, str],
) -> EvalCaseResult:
    q = effective_question_text(case, by_id)
    ranked = rank_chunks_for_question(
        question=q,
        chunks=chunks,
        user_group=case.user_group,
        max_chunks=max_chunks,
    )
    target = case.expected_source_doc
    assert target is not None
    want = filename_to_id.get(target)
    got_ids = {c.doc_id for c in ranked}
    excluded = want is not None and want not in got_ids
    return EvalCaseResult(
        case_id=case.case_id,
        category=case.category,
        retrieval_skipped=True,
        confidential_excluded=excluded,
        notes=[] if excluded else ["confidential_chunk_in_top_k_without_clearance"],
    )


async def _eval_retrieval_categories(
    case: EvalTestCase,
    by_id: dict[str, EvalTestCase],
    chunks: list[RetrievedChunk],
    manifest: dict[str, Any],
    k: int,
    max_chunks: int,
    llm: LlmClient | None,
    llm_judge_enabled: bool,
) -> EvalCaseResult:
    q = effective_question_text(case, by_id)
    ranked = rank_chunks_for_question(
        question=q,
        chunks=chunks,
        user_group=case.user_group,
        max_chunks=max_chunks,
    )
    retrieved_ids = [c.doc_id for c in ranked]
    prec, rec = _retrieval_precision_recall(case, manifest, retrieved_ids, k)
    stub = build_stub_answer(ranked, case.expected_answer_contains)
    cites = stub_citations_from_chunks(ranked)
    cite_acc, cite_issues = citation_accuracy_ratio(cites, ranked)
    g_score, c_score, m_score = await _maybe_llm_judge_scores(
        llm_judge_enabled,
        llm,
        q,
        stub,
        ranked,
    )
    contains_hits = sum(1 for phrase in case.expected_answer_contains if phrase.lower() in stub.lower())
    return EvalCaseResult(
        case_id=case.case_id,
        category=case.category,
        precision_at_k=prec,
        recall_at_k=rec,
        citation_accuracy=cite_acc,
        groundedness=g_score,
        correctness=c_score,
        completeness=m_score,
        answer_contains_hits=contains_hits,
        notes=list(cite_issues),
    )


def _retrieval_precision_recall(
    case: EvalTestCase,
    manifest: dict[str, Any],
    retrieved_ids: list[str],
    k: int,
) -> tuple[float | None, float | None]:
    """Compute precision@k and recall@k when a gold document id is known."""
    target_name = case.expected_source_doc
    relevant: set[str] = set()
    if target_name:
        rid = doc_id_for_filename(manifest, target_name)
        if rid:
            relevant.add(rid)
    if not (case.category in RETRIEVAL_SCORE_CATEGORIES and relevant):
        return (None, None)
    prec = precision_at_k(relevant, retrieved_ids, k)
    rec = recall_at_k(relevant, retrieved_ids, k)
    return (prec, rec)


async def _maybe_llm_judge_scores(
    llm_judge_enabled: bool,
    llm: LlmClient | None,
    question: str,
    answer: str,
    ranked: list[RetrievedChunk],
) -> tuple[float | None, float | None, float | None]:
    """Optionally score the stub answer with a judge model."""
    if not llm_judge_enabled or llm is None:
        return (None, None, None)
    evidence = "\n\n".join(c.text[:1200] for c in ranked[:3])
    judge = await score_answer_with_llm_judge(
        question=question,
        answer=answer,
        evidence_excerpt=evidence,
        llm=llm,
    )
    if judge is None:
        return (None, None, None)
    g_score = _float_or_none(judge.get("groundedness"))
    c_score = _float_or_none(judge.get("correctness"))
    m_score = _float_or_none(judge.get("completeness"))
    return (g_score, c_score, m_score)


def default_paths(repo_root: Path) -> EvaluationPaths:
    """Standard layout under ``eval/``."""
    return EvaluationPaths(
        repo_root=repo_root,
        test_set=repo_root / "eval" / "test_set.jsonl",
        manifest=repo_root / "eval" / "sample_docs" / "manifest.json",
        sample_dir=repo_root / "eval" / "sample_docs",
        results_dir=repo_root / "eval" / "results",
    )


def write_report(report: EvalReport, results_dir: Path) -> Path:
    """
    Serialize report to ``eval/results/eval_YYYY-MM-DD.json`` (UTC date).

    Args:
        report: Evaluation output model.
        results_dir: Directory to create if missing.

    Returns:
        Path to the written file.
    """
    results_dir.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = results_dir / f"eval_{day}.json"
    out.write_text(
        json.dumps(report.model_dump(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    logger.info("Wrote evaluation report", extra={"path": str(out)})
    return out
