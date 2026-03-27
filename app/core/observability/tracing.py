"""
Optional LangSmith tracing helpers (no-op when API key is unset or package missing).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar, cast

from app.config import settings

F = TypeVar("F", bound=Callable[..., Any])


def maybe_trace(name: str) -> Callable[[F], F]:
    """
    Return a decorator that wraps ``fn`` with LangSmith ``traceable`` when configured.

    Args:
        name: Logical span name for the trace.

    Returns:
        Decorator that leaves ``fn`` unchanged when tracing is disabled.
    """

    def deco(fn: F) -> F:
        if not settings.langsmith_api_key:
            return fn
        try:
            from langsmith import traceable
        except ImportError:
            return fn
        wrapped = traceable(name=name)(fn)
        return cast(F, wrapped)

    return deco
