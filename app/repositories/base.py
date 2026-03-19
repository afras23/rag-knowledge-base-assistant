"""
Base repository abstraction for async SQLAlchemy access.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession


class BaseRepository:
    """Base repository holding an async SQLAlchemy session dependency."""

    def __init__(self, db_session: AsyncSession) -> None:
        """
        Initialize repository with an async DB session.

        Args:
            db_session: Async SQLAlchemy session.
        """
        self._db_session = db_session

    @property
    def db_session(self) -> AsyncSession:
        """Return the injected async DB session."""
        return self._db_session
