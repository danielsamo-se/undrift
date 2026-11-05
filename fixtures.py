"""Fictional Shiftly seed data: the four preflight documents and the returns policy history."""

from __future__ import annotations

from datetime import date, datetime

from models import Document, Policy

POLICY_ID = "shiftly-returns"
POLICY_TITLE = "Shiftly voluntary returns"

SEED_POLICY = Policy(
    id=POLICY_ID,
    version=1,
    policy_type="voluntary_return",
    voluntary_return_days=60,
    effective_from=date(2025, 1, 1),
    description=(
        "Shiftly offers a voluntary return period of 60 days. This voluntary period is separate "
        "from the statutory 14-day EU right of withdrawal, which this policy does not change."
    ),
)

SHIFTLY_RETURNS_CHANGE = {
    "voluntary_return_days": 30,
    "effective_from": date(2026, 11, 4),
    "description": (
        "Shiftly offers a voluntary return period of 30 days for purchases made on or after "
        "2026-11-04 (previously 60 days). Purchases made before 2026-11-04 keep the voluntary "
        "return period that was promised at the time of purchase. This voluntary period is "
        "separate from the statutory 14-day EU right of withdrawal, which this policy does not "
        "change."
    ),
}

CHANNELS: dict[str, tuple[str, str]] = {
    "website-faq": ("Website FAQ", "public"),
    "legal-terms": ("Legal terms", "public"),
    "support-crm": ("Support CRM", "internal"),
    "website-shipping": ("Website shipping page", "public"),
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

# Expected classification, used only by spike.py and tests to grade an agent's answer.
EXPECTED_CLASSIFICATION: dict[str, str] = {
    "doc-001": "OUTDATED",
    "doc-002": "VALID_DISTINCT",
    "doc-003": "EXISTING_COMMITMENT",
    "doc-004": "UNRELATED",
}
