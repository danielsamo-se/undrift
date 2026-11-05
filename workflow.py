"""Policy change -> impact detection -> reviewable proposal -> authorized review -> apply.

Who may do what:
- Agents (MCP clients, Cominty replies) can only *submit* proposals; they land in pending_review.
- Changing a policy, approving, rejecting and applying require the local review token
  (UNDRIFT_REVIEW_TOKEN), checked on every call. Nothing an agent writes into a proposal,
  reason or reply can count as approval.
- Applying re-checks the document version and content (optimistic concurrency) and only
  ever changes the local demo database.
"""

from __future__ import annotations

import difflib
import hashlib
import hmac
import json
import os
import re
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

import config
from models import Policy, Proposal
from store import Store, now

MAX_CONTENT = 20_000
MAX_REASON = 2_000
OPEN_STATUSES = ("pending_review", "approved")
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
STATUTORY_WITHDRAWAL_DAYS = 14
DAYS_RE = re.compile(r"\b(\d{1,3})\s*-?\s*days?\b", re.I)
PROTECTED_RULES = (
    ("statutory_right", re.compile(r"\b(statutory|withdrawal)\b", re.I)),
    ("existing_commitment", re.compile(
        r"\border\s*#\s*\d+|\b(placed|made|purchased|bought)\s+before\b|\bpromised\b", re.I)),
)
CATEGORIES = {"OUTDATED", "VALID_DISTINCT", "EXISTING_COMMITMENT", "UNRELATED", "UNKNOWN"}


class WorkflowError(Exception):
    status = 400
    code = "invalid_request"

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.details = details


class NotFound(WorkflowError):
    status, code = 404, "not_found"


class Unauthorized(WorkflowError):
    status, code = 401, "unauthorized"


class Conflict(WorkflowError):
    status, code = 409, "conflict"


class GuardrailViolation(WorkflowError):
    status, code = 422, "guardrail_violation"


@dataclass(frozen=True)
class Sentence:
    index: int
    text: str
    stale: bool
    protected: str | None


@dataclass
class Candidate:
    document_id: str
    version: int
    channel: str
    stale_sentences: list[int] = field(default_factory=list)
    protected: dict[int, str] = field(default_factory=dict)

    @property
    def actionable(self) -> bool:
        return any(i not in self.protected for i in self.stale_sentences)


def split_sentences(text: str) -> list[str]:
    return [s for s in SENTENCE_SPLIT.split(text.strip()) if s]


def protection(sentence: str) -> str | None:
    return next((name for name, rule in PROTECTED_RULES if rule.search(sentence)), None)


def analyze(content: str, stale: re.Pattern[str] | None) -> list[Sentence]:
    return [Sentence(i, s, bool(stale and stale.search(s)), protection(s))
            for i, s in enumerate(split_sentences(content))]


def days_pattern(values: set[int]) -> re.Pattern[str] | None:
    if not values:
        return None
    return re.compile(rf"\b({'|'.join(map(str, sorted(values)))})\s*-?\s*days?\b", re.I)


def _policy_context(store: Store, policy_id: str,
                    conn: sqlite3.Connection) -> tuple[Policy, re.Pattern[str] | None]:
    """The current policy and a pattern for every return period an older version stated."""
    current = store.policy(policy_id, conn=conn)
    if current is None:
        raise NotFound(f"unknown policy {policy_id!r}")
    older = {r[0] for r in conn.execute(
        "SELECT voluntary_return_days FROM policy_versions WHERE policy_id = ? AND version < ?",
        (policy_id, current.version))}
    return current, days_pattern(older - {current.voluntary_return_days})


def detect_candidates(store: Store, policy_id: str,
                      conn: sqlite3.Connection | None = None) -> list[Candidate]:
    """Deterministic pre-scan: documents whose current text states a superseded period.

    An optimization and review hint, not a classification and not a gate: the agent may
    classify and correct documents this scan misses. A candidate whose stale sentences are
    all protected (statutory rights, existing commitments) is not actionable.
    """
    with store.read(conn) as c:
        _, stale = _policy_context(store, policy_id, c)
        out = []
        for doc in store.documents(c):
            sentences = analyze(doc.content, stale)
            hits = [s.index for s in sentences if s.stale]
            if hits:
                out.append(Candidate(doc.id, doc.version, doc.channel, hits,
                                     {s.index: s.protected for s in sentences
                                      if s.stale and s.protected}))
        return out


def check_guardrails(original: str, proposed: str, *, current_days: int,
                     stale: re.Pattern[str] | None,
                     evidence: str | None = None) -> tuple[list[dict], list[dict]]:
    """Validate a correction; return (violations, review_flags).

    Rewritable: unprotected sentences the prefilter marks stale, plus unprotected sentences
    the agent quotes as evidence (so wording the prefilter misses can still be fixed).
    Everything else in the original is kept verbatim. The result must state the current
    period, keep no unprotected superseded period, and any new statutory statement must
    give the statutory period.
    """
    if not proposed.strip():
        return [{"rule": "empty_content"}], []
    if proposed == original:
        return [{"rule": "no_change"}], []
    violations: list[dict[str, Any]] = []
    flags: list[dict[str, Any]] = []
    old = analyze(original, stale)
    new = analyze(proposed, stale)
    kept = {s.text for s in new}
    cited = (evidence or "").strip()
    replaced = False
    for s in old:
        if s.text in kept:
            continue
        if s.protected:
            violations.append({"rule": s.protected, "sentence": s.index})
        elif s.stale:
            replaced = True
        elif len(cited) >= 8 and (s.text in cited or cited in s.text):
            replaced = True
            flags.append({"flag": "rewrites_sentence_missed_by_prefilter", "sentence": s.index})
        else:
            violations.append({"rule": "unrelated_content", "sentence": s.index})
    if not replaced and not violations:
        violations.append({"rule": "nothing_replaced"})
    if not any(s.stale for s in old):
        flags.append({"flag": "document_not_flagged_by_prefilter"})

    current = days_pattern({current_days})
    original_texts = {s.text for s in old}
    states_current = False
    for s in new:
        if s.stale and not s.protected:
            violations.append({"rule": "outdated_statement_remains", "proposed_sentence": s.index})
        if s.protected == "statutory_right" and s.text not in original_texts:
            wrong = sorted({int(d) for d in DAYS_RE.findall(s.text)} - {STATUTORY_WITHDRAWAL_DAYS})
            if wrong:
                violations.append({"rule": "incorrect_statutory_statement",
                                   "proposed_sentence": s.index, "days": wrong})
        if not s.protected and current and current.search(s.text):
            states_current = True
    if not states_current:
        violations.append({"rule": "correction_missing", "expected_days": current_days})
    return violations, flags


def unified_diff(document_id: str, version: int, original: str, proposed: str) -> str:
    return "\n".join(difflib.unified_diff(
        original.splitlines(), proposed.splitlines(),
        fromfile=f"{document_id}@v{version}", tofile=f"{document_id}@proposed", lineterm=""))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def authorize(store: Store, token: str | None, action: str, subject: tuple[str, str]) -> str:
    """Return the reviewer name for a valid review token; record and raise otherwise."""
    expected = os.environ.get("UNDRIFT_REVIEW_TOKEN", "").strip()
    if len(expected) < config.MIN_SECRET_LENGTH:
        reason = "review token is not configured on this machine"
    elif not token or not hmac.compare_digest(_sha(token.strip()), _sha(expected)):
        reason = "missing or invalid review token"
    else:
        return os.environ.get("UNDRIFT_REVIEWER", "").strip() or "local-reviewer"
    store.record_event("auth.denied", actor="unauthenticated", status="denied", subject=subject,
                       metadata={"action": action}, error=reason)
    raise Unauthorized(reason)


def change_policy(store: Store, policy_id: str, *, voluntary_return_days: int,
                  effective_from: date, description: str,
                  token: str | None) -> tuple[Policy, list[Candidate]]:
    reviewer = authorize(store, token, "policy.change", ("policy", policy_id))
    if not 1 <= voluntary_return_days <= 365:
        raise WorkflowError("voluntary_return_days must be between 1 and 365")
    if not description.strip() or len(description) > 4_000:
        raise WorkflowError("description must be 1-4000 characters")
    with store.write() as conn:
        current = store.policy(policy_id, conn=conn)
        if current is None:
            raise NotFound(f"unknown policy {policy_id!r}")
        if (current.voluntary_return_days, current.effective_from, current.description) == (
                voluntary_return_days, effective_from, description):
            raise Conflict("the new version is identical to the current one")
        version = current.version + 1
        conn.execute("INSERT INTO policy_versions VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (policy_id, version, voluntary_return_days, effective_from.isoformat(),
                      description, now(), f"reviewer:{reviewer}"))
        conn.execute("UPDATE policies SET current_version = ? WHERE id = ?", (version, policy_id))
        store.add_event(conn, "policy.version_created", actor=f"reviewer:{reviewer}",
                        subject=("policy", policy_id), metadata={
                            "policy_id": policy_id, "version": version,
                            "previous_version": current.version,
                            "voluntary_return_days": voluntary_return_days,
                            "previous_voluntary_return_days": current.voluntary_return_days,
                            "effective_from": effective_from.isoformat()})
        candidates = detect_candidates(store, policy_id, conn)
        store.add_event(conn, "impact.candidates_detected", actor="system",
                        subject=("policy", policy_id), metadata={
                            "policy_id": policy_id, "policy_version": version,
                            "documents_scanned": len(store.documents(conn)),
                            "candidates": [{**asdict(c), "actionable": c.actionable}
                                           for c in candidates]})
        return store.policy(policy_id, conn=conn), candidates  # type: ignore[return-value]


def submit_proposal(store: Store, *, document_id: str, base_version: int, policy_id: str,
                    policy_version: int, proposed_content: str, reason: str, actor: str,
                    provenance: dict[str, Any] | None = None,
                    evidence: str | None = None) -> Proposal:
    """Create a pending_review proposal. Never changes the document itself.

    `evidence` is the agent's verbatim quote of the outdated text; it authorizes rewriting
    the quoted sentences even when the deterministic prefilter did not flag them.
    """
    provenance = config.safe_metadata(provenance or {})
    meta = {"document_id": document_id, "base_version": base_version, "policy_id": policy_id,
            "policy_version": policy_version, "provenance": provenance}
    try:
        if type(base_version) is not int or type(policy_version) is not int:
            raise WorkflowError("base_version and policy_version must be integers")
        if not isinstance(proposed_content, str) or len(proposed_content) > MAX_CONTENT:
            raise WorkflowError(f"proposed_content must be a string of at most {MAX_CONTENT} chars")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > MAX_REASON:
            raise WorkflowError(f"reason must be 1-{MAX_REASON} characters")
        with store.write() as conn:
            doc = store.document(document_id, conn=conn)
            if doc is None:
                raise NotFound(f"unknown document {document_id!r}")
            if doc.version != base_version:
                raise Conflict(f"stale document version: proposal based on v{base_version}, "
                               f"current is v{doc.version}", current_version=doc.version)
            policy, stale = _policy_context(store, policy_id, conn)
            if policy.version != policy_version:
                raise Conflict(f"stale policy version: proposal based on v{policy_version}, "
                               f"current is v{policy.version}", current_version=policy.version)
            open_ = [p.id for p in store.proposals(document_id=document_id, conn=conn)
                     if p.status in OPEN_STATUSES]
            if open_:
                raise Conflict("an open proposal already exists for this document",
                               proposal_ids=open_)
            violations, flags = check_guardrails(
                doc.content, proposed_content, current_days=policy.voluntary_return_days,
                stale=stale, evidence=evidence if isinstance(evidence, str) else None)
            if violations:
                raise GuardrailViolation("proposal violates content guardrails",
                                         violations=violations, review_flags=flags)
            diff = unified_diff(document_id, base_version, doc.content, proposed_content)
            proposal_id = f"prop-{uuid.uuid4().hex[:12]}"
            conn.execute(
                "INSERT INTO proposals (id, document_id, base_version, policy_id, policy_version, "
                "original_content, proposed_content, diff, reason, status, created_at, created_by, "
                "provenance) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_review', ?, ?, ?)",
                (proposal_id, document_id, base_version, policy_id, policy_version, doc.content,
                 proposed_content, diff, reason.strip(), now(), config.safe_text(actor, 120),
                 json.dumps(provenance, sort_keys=True)))
            lines = diff.splitlines()[2:]
            store.add_event(conn, "proposal.created", actor=actor,
                            subject=("proposal", proposal_id), metadata={
                                **meta, "proposal_id": proposal_id,
                                "lines_added": sum(ln.startswith("+") for ln in lines),
                                "lines_removed": sum(ln.startswith("-") for ln in lines),
                                "original_sha256": config.fingerprint(doc.content),
                                "proposed_sha256": config.fingerprint(proposed_content),
                                "review_flags": flags})
            return store.proposal(proposal_id, conn)  # type: ignore[return-value]
    except WorkflowError as exc:
        store.record_event("proposal.submission_failed", actor=actor, status="error",
                           subject=("document", document_id),
                           metadata={**meta, "code": exc.code, **exc.details}, error=str(exc))
        raise


def _review(store: Store, proposal_id: str, token: str | None, action: str,
            allowed_from: tuple[str, ...], new_status: str, note: str | None) -> Proposal:
    reviewer = authorize(store, token, f"proposal.{action}", ("proposal", proposal_id))
    if note is not None and len(note) > MAX_REASON:
        raise WorkflowError(f"note must be at most {MAX_REASON} characters")
    actor = f"reviewer:{reviewer}"
    try:
        with store.write() as conn:
            proposal = store.proposal(proposal_id, conn)
            if proposal is None:
                raise NotFound(f"unknown proposal {proposal_id!r}")
            if proposal.status not in allowed_from:
                raise Conflict(f"cannot {action} a proposal in status {proposal.status}")
            doc = store.document(proposal.document_id, conn=conn)
            if new_status != "rejected" and (
                    doc is None or doc.version != proposal.base_version
                    or _sha(doc.content) != _sha(proposal.original_content)):
                raise Conflict("document changed since the proposal was created: "
                               f"proposal based on v{proposal.base_version}, "
                               f"current is v{doc.version if doc else None}",
                               current_version=doc.version if doc else None)
            policy = store.policy(proposal.policy_id, conn=conn)
            if new_status != "rejected" and (
                    policy is None or policy.version != proposal.policy_version):
                raise Conflict("policy changed since the proposal was created: "
                               f"proposal based on v{proposal.policy_version}, "
                               f"current is v{policy.version if policy else None}",
                               current_policy_version=policy.version if policy else None)
            fields: dict[str, Any] = {"status": new_status}
            if action == "apply":
                version = proposal.base_version + 1
                conn.execute("INSERT INTO document_versions VALUES (?, ?, ?, ?, ?)",
                             (proposal.document_id, version, proposal.proposed_content, now(),
                              f"{actor} via {proposal_id}"))
                updated = conn.execute(
                    "UPDATE documents SET current_version = ? WHERE id = ? AND current_version = ?",
                    (version, proposal.document_id, proposal.base_version)).rowcount
                if updated != 1:
                    raise Conflict("document version changed concurrently")
                fields.update(applied_version=version, applied_at=now())
                store.add_event(conn, "document.version_created", actor=actor,
                                subject=("document", proposal.document_id), metadata={
                                    "document_id": proposal.document_id, "version": version,
                                    "previous_version": proposal.base_version,
                                    "proposal_id": proposal_id})
            else:
                fields.update(reviewed_by=reviewer, reviewed_at=now(), review_note=note)
            sets = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(f"UPDATE proposals SET {sets} WHERE id = ?", (*fields.values(), proposal_id))
            store.add_event(conn, f"proposal.{new_status}", actor=actor,
                            subject=("proposal", proposal_id), metadata={
                                "proposal_id": proposal_id, "document_id": proposal.document_id,
                                "from_status": proposal.status, "note": note or ""})
            return store.proposal(proposal_id, conn)  # type: ignore[return-value]
    except WorkflowError as exc:
        store.record_event(f"proposal.{action}_failed", actor=actor, status="error",
                           subject=("proposal", proposal_id),
                           metadata={"code": exc.code, **exc.details}, error=str(exc))
        raise


def approve(store: Store, proposal_id: str, *, token: str | None, note: str | None = None) -> Proposal:
    return _review(store, proposal_id, token, "approve", ("pending_review",), "approved", note)


def reject(store: Store, proposal_id: str, *, token: str | None, note: str | None = None) -> Proposal:
    return _review(store, proposal_id, token, "reject", OPEN_STATUSES, "rejected", note)


def apply(store: Store, proposal_id: str, *, token: str | None) -> Proposal:
    return _review(store, proposal_id, token, "apply", ("approved",), "applied", None)


def parse_verdict(reply: str) -> dict[str, Any] | None:
    blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", reply, flags=re.DOTALL)
    for block in reversed(blocks):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def ingest_agent_reply(store: Store, reply: str, *, actor: str,
                       provenance: dict[str, Any]) -> dict[str, Any]:
    """Record an agent's classifications and submit its corrections as pending proposals.

    Only `category`, `document_version`, `reason`/`evidence` and `proposed_content` are read
    from each document entry. Any status or approval claims in the reply are ignored.
    """
    summary: dict[str, Any] = {"classifications": {}, "proposals": [], "errors": []}
    verdict = parse_verdict(reply)
    if verdict is None:
        store.record_event("agent.reply_unparsed", actor=actor, status="error",
                           metadata={"provenance": provenance, "reply_length": len(reply)},
                           error="no JSON verdict block in the agent reply")
        summary["errors"].append({"error": "no JSON verdict block"})
        return summary

    reported = verdict.get("policy") if isinstance(verdict.get("policy"), dict) else {}
    policy_id = str(reported.get("id", ""))
    try:
        with store.read() as conn:
            policy, _ = _policy_context(store, policy_id, conn)
            candidates = {c.document_id: c for c in detect_candidates(store, policy_id, conn)}
            known_docs = set(store.document_ids())
    except NotFound as exc:
        store.record_event("agent.reply_rejected", actor=actor, status="error",
                           metadata={"provenance": provenance, "policy_id": policy_id},
                           error=str(exc))
        summary["errors"].append({"error": str(exc)})
        return summary
    policy_matches = reported.get("version") == policy.version
    store.record_event("agent.verdict_received", actor=actor, subject=("policy", policy.id),
                       status="ok" if policy_matches else "error", metadata={
                           "provenance": provenance, "reported_policy_version": reported.get("version"),
                           "current_policy_version": policy.version,
                           "changes_made_claimed": verdict.get("changes_made")},
                       error=None if policy_matches else "agent analysed a non-current policy version")
    if not policy_matches:
        summary["errors"].append({"error": "policy version mismatch"})
        return summary

    entries = verdict.get("documents") if isinstance(verdict.get("documents"), dict) else {}
    for document_id, entry in sorted(entries.items()):
        entry = entry if isinstance(entry, dict) else {}
        category = str(entry.get("category", "UNKNOWN")).upper()
        if document_id not in known_docs or category not in CATEGORIES:
            summary["errors"].append({"document_id": document_id, "error": "unknown id or category"})
            continue
        cand = candidates.get(document_id)
        scan = "actionable" if cand and cand.actionable else "protected" if cand else "not_stale"
        agrees = (category == "OUTDATED") == (scan == "actionable")
        summary["classifications"][document_id] = category
        store.record_event("agent.classification", actor=actor, subject=("document", document_id),
                           metadata={"category": category, "deterministic_scan": scan,
                                     "agrees_with_scan": agrees, "provenance": provenance,
                                     "document_version": entry.get("document_version"),
                                     "policy_version": policy.version})
        proposed = entry.get("proposed_content")
        if category != "OUTDATED" or not isinstance(proposed, str):
            continue
        try:
            proposal = submit_proposal(
                store, document_id=document_id, base_version=entry.get("document_version"),
                policy_id=policy.id, policy_version=policy.version, proposed_content=proposed,
                reason=str(entry.get("reason") or entry.get("evidence") or "").strip()[:MAX_REASON],
                actor=actor, provenance=provenance, evidence=entry.get("evidence"))
            summary["proposals"].append(proposal.id)
        except WorkflowError as exc:
            summary["errors"].append({"document_id": document_id, "error": str(exc)})
    return summary
