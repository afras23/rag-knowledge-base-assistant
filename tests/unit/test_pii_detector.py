"""
Unit tests for PII detection.
"""

from __future__ import annotations

from app.ai.pii_detector import PiiDetector
from app.config import Settings


def test_email() -> None:
    detector = PiiDetector(Settings(pii_policy="warn"))
    r = detector.scan_text("Contact me at user.name@example.com please")
    assert r.has_pii is True
    assert "email" in r.categories


def test_phone_us() -> None:
    detector = PiiDetector(Settings(pii_policy="warn"))
    r = detector.scan_text("Call (202) 555-0199 for help")
    assert r.has_pii is True
    assert "phone" in r.categories


def test_phone_uk() -> None:
    detector = PiiDetector(Settings(pii_policy="warn"))
    r = detector.scan_text("Mobile 07700900123")
    assert r.has_pii is True
    assert "phone" in r.categories


def test_ssn() -> None:
    detector = PiiDetector(Settings(pii_policy="warn"))
    r = detector.scan_text("SSN 123-45-6789 on file")
    assert r.has_pii is True
    assert "ssn" in r.categories


def test_block_mode_refuses_semantics() -> None:
    """Block policy: scan reports PII; caller should reject (generation service)."""
    detector = PiiDetector(Settings(pii_policy="block"))
    r = detector.scan_text("x@y.com")
    assert r.has_pii is True
    assert "email" in r.categories


def test_redact_mode_replaces() -> None:
    detector = PiiDetector(Settings(pii_policy="redact"))
    r = detector.scan_text("Email a@b.co for info")
    assert r.has_pii is True
    assert "[REDACTED_EMAIL]" in r.redacted_text
    assert "a@b.co" not in r.redacted_text


def test_clean_passes() -> None:
    detector = PiiDetector(Settings(pii_policy="warn"))
    r = detector.scan_text("No sensitive data in this sentence.")
    assert r.has_pii is False
    assert r.redacted_text == "No sensitive data in this sentence."
