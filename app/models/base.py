"""
SQLAlchemy declarative base used for all ORM models.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base class for ORM models."""
