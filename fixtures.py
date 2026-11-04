"""Fictional Shiftly test fixtures for the preflight. In-memory, read-only."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class Policy(BaseModel):
    id: str = Field(description="Policy identifier")
    version: int = Field(description="Monotonic policy version")
    policy_type: Literal["voluntary_return"] = Field(description="Kind of policy")
    voluntary_return_days: int = Field(description="Voluntary return period for new purchases, in days")
    effective_from: date = Field(description="First purchase date (inclusive) this version applies to")
    description: str = Field(description="Authoritative human-readable policy text")


class Document(BaseModel):
    id: str = Field(description="Document identifier")
    channel: str = Field(description="Channel the content is published in")
    title: str = Field(description="Document title")
    content: str = Field(description="Document text exactly as published")
    version: int = Field(description="Document version")
    updated_at: datetime = Field(description="Last update time (UTC)")


POLICIES: dict[str, Policy] = {
    "shiftly-returns": Policy(
        id="shiftly-returns",
        version=2,
        policy_type="voluntary_return",
        voluntary_return_days=30,
        effective_from=date(2026, 11, 4),
        description=(
            "Shiftly offers a voluntary return period of 30 days for purchases made on or after "
            "2026-11-04 (previously 60 days). Purchases made before 2026-11-04 keep the voluntary "
            "return period that was promised at the time of purchase. This voluntary period is "
            "separate from the statutory 14-day EU right of withdrawal, which this policy does not "
            "change."
        ),
    ),
}

DOCUMENTS: dict[str, Document] = {
    "doc-001": Document(
        id="doc-001",
        channel="website-faq",
        title="Returns FAQ",
        content="All customers can return items within 60 days.",
        version=3,
        updated_at=datetime.fromisoformat("2026-03-12T09:00:00+00:00"),
    ),
    "doc-002": Document(
        id="doc-002",
        channel="legal-terms",
        title="Right of withdrawal",
        content="Customers have a statutory 14-day withdrawal right.",
        version=1,
        updated_at=datetime.fromisoformat("2025-06-01T08:00:00+00:00"),
    ),
    "doc-003": Document(
        id="doc-003",
        channel="support-crm",
        title="Order #1001 note",
        content=(
            "Order #1001 was placed before the policy change and retains its promised "
            "60-day return period."
        ),
        version=1,
        updated_at=datetime.fromisoformat("2026-11-04T10:15:00+00:00"),
    ),
    "doc-004": Document(
        id="doc-004",
        channel="website-shipping",
        title="Shipping",
        content="Free shipping on orders above 50 EUR.",
        version=2,
        updated_at=datetime.fromisoformat("2026-01-20T12:00:00+00:00"),
    ),
}

# Expected classification, used only by spike.py to grade the agent's answer.
EXPECTED_CLASSIFICATION: dict[str, str] = {
    "doc-001": "OUTDATED",
    "doc-002": "VALID_DISTINCT",
    "doc-003": "EXISTING_COMMITMENT",
    "doc-004": "UNRELATED",
}
