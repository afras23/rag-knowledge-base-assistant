"""
Offline and scripted evaluation helpers (Phase 10): retrieval metrics, citations, reports.
"""

from app.evaluation.citations import citation_accuracy_ratio
from app.evaluation.metrics import precision_at_k, recall_at_k
from app.evaluation.schema import EvalReport, EvalTestCase

__all__ = [
    "citation_accuracy_ratio",
    "precision_at_k",
    "recall_at_k",
    "EvalReport",
    "EvalTestCase",
]
