"""
Unit tests for deterministic query rewrite triggers.
"""

from __future__ import annotations

import logging

import pytest

from app.services.retrieval.query_rewriter import QueryRewriter


def test_short_query_triggers_rewrite() -> None:
    rewriter = QueryRewriter()
    assert rewriter.analyze("a b c").should_rewrite is True


def test_pronoun_query_triggers_rewrite() -> None:
    rewriter = QueryRewriter()
    assert rewriter.analyze("Does it cover flights?").should_rewrite is True


def test_normal_question_not_rewritten() -> None:
    rewriter = QueryRewriter()
    analysis = rewriter.analyze("What is the reimbursement policy for domestic travel?")
    assert analysis.should_rewrite is False


def test_single_noun_phrase_triggers_rewrite() -> None:
    rewriter = QueryRewriter()
    assert rewriter.analyze("travel policy").should_rewrite is True


@pytest.mark.anyio
async def test_rewrite_logs_reason(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    rewriter = QueryRewriter()
    await rewriter.rewrite("a b")
    assert "Query rewrite recommended by heuristic" in caplog.text
    record = caplog.records[0]
    assert getattr(record, "reason", None) == "short_query"
