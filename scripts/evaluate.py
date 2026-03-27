#!/usr/bin/env python3
"""
CLI entrypoint for the Phase 10 evaluation pipeline.

Writes ``eval/results/eval_YYYY-MM-DD.json`` (UTC date) with retrieval, citation,
guardrail, and optional LLM-judge metrics.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from app.evaluation.runner import default_paths, run_evaluation, write_report

logger = logging.getLogger(__name__)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run RAG evaluation over eval/test_set.jsonl")
    parser.add_argument("--k", type=int, default=5, help="Rank cut-off for precision/recall")
    parser.add_argument("--max-chunks", type=int, default=5, help="Offline retrieval depth")
    parser.add_argument(
        "--with-llm",
        action="store_true",
        help="Enable LLM-as-judge when OPENAI_API_KEY is configured",
    )
    return parser.parse_args(argv)


def main() -> int:
    """Parse CLI args, run async evaluation, write JSON report."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    args = _parse_args(sys.argv[1:])
    repo_root = Path(__file__).resolve().parent.parent
    paths = default_paths(repo_root)
    report = asyncio.run(
        run_evaluation(
            paths=paths,
            k=args.k,
            max_chunks=args.max_chunks,
            with_llm=args.with_llm,
        ),
    )
    out_path = write_report(report, paths.results_dir)
    logger.info("Evaluation complete", extra={"report_path": str(out_path)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
