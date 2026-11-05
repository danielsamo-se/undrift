"""Pydantic models shared by the store, the MCP server and the review API."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ProposalStatus = Literal["pending_review", "approved", "rejected", "applied"]


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


class Proposal(BaseModel):
    id: str
    document_id: str
    base_version: int
    policy_id: str
    policy_version: int
    original_content: str
    proposed_content: str
    diff: str
    reason: str
    status: ProposalStatus
    created_at: datetime
    created_by: str
    provenance: dict[str, Any]
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    review_note: str | None = None
    applied_version: int | None = None
    applied_at: datetime | None = None


class Event(BaseModel):
    seq: int
    ts: datetime
    type: str
    actor: str
    status: Literal["ok", "error", "denied"]
    subject_type: str | None
    subject_id: str | None
    metadata: dict[str, Any]
    error: str | None
