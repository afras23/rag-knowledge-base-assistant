"""
Integration-test helpers (FastAPI dependency overrides, etc.).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from app.main import app


@contextmanager
def admin_dependency_overrides(overrides: Mapping[Any, Any]) -> Iterator[None]:
    """
    Register FastAPI dependency overrides for admin routes and restore afterward.

    Args:
        overrides: Mapping from dependency callable (e.g. admin._get_pipeline) to override.

    Yields:
        None while overrides are active.
    """
    keys = tuple(overrides.keys())
    for dependency_callable, override_callable in overrides.items():
        app.dependency_overrides[dependency_callable] = override_callable
    try:
        yield
    finally:
        for dependency_callable in keys:
            app.dependency_overrides.pop(dependency_callable, None)
