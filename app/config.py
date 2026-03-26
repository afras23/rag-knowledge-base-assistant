"""
Application configuration loaded from environment variables.
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _parse_csv_list(value: str) -> list[str]:
    parts = [part.strip() for part in value.split(",")]
    return [part for part in parts if part]


class Settings(BaseSettings):
    """RAG Knowledge Base Assistant settings."""

    app_name: str = Field(default="rag-knowledge-base-assistant", description="Application name")
    app_env: Literal["development", "staging", "production"] = Field(
        default="development",
        description="Deployment environment",
    )
    debug: bool = Field(default=True, description="Enable debug mode (affects docs URL and CORS defaults)")

    app_version: str = Field(default="0.1.0", description="Application version for health endpoints")
    api_prefix: str = Field(default="/api/v1", description="API prefix used by all routes")

    log_level: str = Field(default="INFO", description="Python logging level (e.g., INFO, DEBUG)")

    database_url: str = Field(
        default="postgresql+asyncpg://anesah@localhost:5432/postgres",
        description="Async SQLAlchemy database URL (postgresql+asyncpg://...)",
    )

    chroma_persist_directory: str = Field(
        default="./.chroma",
        description="Local filesystem directory for persistent ChromaDB data",
    )
    chroma_collection_prefix: str = Field(
        default="",
        description="Optional prefix applied to all Chroma collection names",
    )

    cors_allow_origins: str = Field(
        default="",
        description="Comma-separated list of allowed CORS origins",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("log_level", mode="before")
    @classmethod
    def validate_log_level(cls, value: object) -> str:
        """Normalize and validate log level."""
        normalized = str(value).strip().upper()
        allowed_levels = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        if normalized not in allowed_levels:
            raise ValueError("log_level must be one of CRITICAL/ERROR/WARNING/INFO/DEBUG")
        return normalized

    @property
    def cors_allow_origins_list(self) -> list[str]:
        """CORS origins parsed from the `cors_allow_origins` CSV string."""
        if not self.cors_allow_origins:
            return []
        return _parse_csv_list(self.cors_allow_origins)


settings = Settings()

# Ensure logging defaults are applied early.
logging.basicConfig(level=settings.log_level)
