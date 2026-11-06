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
    run_id: str | None = None
    finding_id: str | None = None
    evidence: str = ""
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


Category = Literal["OUTDATED", "VALID_DISTINCT", "EXISTING_COMMITMENT", "UNRELATED", "UNKNOWN"]


class AgentRun(BaseModel):
    id: str
    policy_id: str
    policy_version: int
    document_ids: list[str]
    status: Literal["open", "completed", "incomplete", "failed", "expired", "interrupted"]
    created_at: datetime
    expires_at: datetime
    closed_at: datetime | None = None
    agent_id: str | None = None
    thread_id: str | None = None
    message_id: str | None = None
    document_versions: dict[str, int] = Field(default_factory=dict)
    lease_until: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class Finding(BaseModel):
    id: str
    run_id: str
    policy_id: str
    policy_version: int
    document_id: str
    document_version: int | None
    category: Category
    evidence: str
    reason: str
    retrieved_during_run: bool
    created_at: datetime
    source: Literal["mcp", "final_json", "legacy"] = "mcp"
    proposed_content: str | None = None
