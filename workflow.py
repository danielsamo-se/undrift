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
import secrets
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import config
from models import AgentRun, Finding, Policy, Proposal
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
# Classification is allowed here; automated correction is not, even after approval.
# Include both unified and archived channel IDs. Never trust an agent-supplied category
# or actor string to decide whether customer records or legal text may be rewritten.
MANUAL_ONLY_CHANNELS = frozenset({'legal', 'crm', 'legal-terms', 'support-crm'})


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


def require_automatic_correction_allowed(doc) -> None:
    if doc.channel in MANUAL_ONLY_CHANNELS:
        raise GuardrailViolation('Legal and CRM documents require a separate manual editing workflow',
                                 violations=[{'rule': 'manual_only_channel', 'channel': doc.channel}])


def verified_finding(run: AgentRun, finding: Finding) -> bool:
    """Only a successful trusted reconciliation can certify an individual conclusion.

    Old completed runs retain their original interpretation. New discovery runs may
    finish incomplete while certifying independent documents in the same transaction.
    An ordinary incomplete/failed/running run never receives this exemption.
    """
    if not finding.retrieved_during_run or finding.category == 'UNKNOWN':
        return False
    return run.status == 'completed' or (
        run.status == 'incomplete' and run.metadata.get('finalization') == 'per_document_v1'
        and finding.document_id in run.metadata.get('verified_document_ids', []))


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


def _return_related(sentence: str) -> bool:
    # Only simple eligibility statements are safe to replace wholesale. Any condition,
    # second clause or processing deadline requires a more precise human edit.
    period = r'(?:\d{1,3}\s*-?\s*days?|two months)'
    return bool(re.fullmatch(
        rf'(?:All customers can return items within {period}|'
        rf'(?:Customers|You) can (?:return items|send items back) within {period}|'
        rf'Items can be returned within {period}|Returns are accepted within {period})[.!]',
        sentence, re.I))


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
                     evidence: str | None = None,
                     effective_from: date | None = None) -> tuple[list[dict], list[dict]]:
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
        elif s.stale and _return_related(s.text):
            replaced = True
        elif (len(cited) >= 8 and evidence is not None and evidence in original
              and (s.text in cited or cited in s.text)
              and _return_related(s.text)):
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
    # New claims must fit a small, explicit policy-only grammar. This intentionally
    # fails closed for wording we cannot validate locally; humans can revise/retry.
    dates = _date_strings(effective_from) if effective_from else []
    date_pattern = '(?:' + '|'.join(re.escape(d) for d in dates) + ')' if dates else r'.+?'
    d = str(current_days)
    correction = re.compile(
        rf"(?:Items purchased on or after {date_pattern} can be returned within {d} days|"
        rf"Customers can return items within {d} days for purchases made on or after {date_pattern}|"
        rf"Items bought from {date_pattern} can be returned within {d} days|"
        rf"For purchases made on or after {date_pattern}, items can be returned within {d} days|"
        rf"Shiftly offers a voluntary return period of {d} days for purchases made on or after {date_pattern}"
        rf"{'' if effective_from else '|Items can be returned within ' + d + ' days'})[.!]", re.I)
    old_days = '|'.join(sorted({d for d in DAYS_RE.findall(original)
                               if stale and stale.search(f'{d} days')})) or r'(?!)'
    transition = re.compile(
        rf"Purchases made before {date_pattern} keep (?:the return period promised at the time of purchase|"
        rf"the voluntary return period promised at the time of purchase|"
        rf"the (?:{old_days})-day return period promised at the time of purchase)[.!]", re.I)
    statutory = {"Your statutory 14-day right of withdrawal is not affected.",
                 "The statutory 14-day EU right of withdrawal is unchanged."}
    for s in new:
        if s.text in original_texts:
            continue
        if not (correction.fullmatch(s.text) or transition.fullmatch(s.text) or s.text in statutory):
            violations.append({"rule": "unsupported_added_claim", "proposed_sentence": s.index})
    if effective_from and not any(correction.fullmatch(s.text) for s in new):
        violations.append({"rule": "effective_date_missing_or_invalid",
                           "effective_from": effective_from.isoformat()})
    # Keep protected/unrelated sentences in order and with their original multiplicity.
    position = 0
    for s in old:
        rewritable = not s.protected and _return_related(s.text) and (s.stale or (
            len(cited) >= 8 and (s.text in cited or cited in s.text)
            and evidence is not None and evidence in original
            and _return_related(s.text)))
        if not rewritable:
            at = proposed.find(s.text, position)
            if at < 0:
                if not any(v.get('sentence') == s.index for v in violations):
                    violations.append({'rule': 'unrelated_content', 'sentence': s.index})
            else:
                position = at + len(s.text)
    return violations, flags


def _date_strings(value: date) -> list[str]:
    return [value.isoformat(), f"{value:%B} {value.day}, {value.year}"]


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
                  token: str | None, expected_version: int | None = None) -> tuple[Policy, list[Candidate]]:
    reviewer = authorize(store, token, "policy.change", ("policy", policy_id))
    if not 1 <= voluntary_return_days <= 365:
        raise WorkflowError("voluntary_return_days must be between 1 and 365")
    if not description.strip() or len(description) > 4_000:
        raise WorkflowError("description must be 1-4000 characters")
    with store.write() as conn:
        current = store.policy(policy_id, conn=conn)
        if current is None:
            raise NotFound(f"unknown policy {policy_id!r}")
        if expected_version is not None and (
                type(expected_version) is not int or expected_version != current.version):
            raise Conflict("policy changed since the editor was opened",
                           current_policy_version=current.version)
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
                    evidence: str | None = None, run_id: str | None = None,
                    finding_id: str | None = None,
                    _conn: sqlite3.Connection | None = None) -> Proposal:
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
        if config.redact(proposed_content) != proposed_content or config.redact(reason) != reason:
            raise WorkflowError("credentials are forbidden in proposal text")
        with store.write(_conn) as conn:
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
                stale=stale, evidence=evidence if isinstance(evidence, str) else None,
                effective_from=policy.effective_from)
            if doc.channel in MANUAL_ONLY_CHANNELS:
                violations.append({'rule': 'manual_only_channel', 'channel': doc.channel})
            if violations:
                raise GuardrailViolation("proposal violates content guardrails",
                                         violations=violations, review_flags=flags)
            diff = unified_diff(document_id, base_version, doc.content, proposed_content)
            proposal_id = f"prop-{uuid.uuid4().hex[:12]}"
            conn.execute(
                "INSERT INTO proposals (id, document_id, base_version, policy_id, policy_version, "
                "original_content, proposed_content, diff, reason, status, created_at, created_by, "
                "provenance, run_id, finding_id, evidence) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_review', ?, ?, ?, ?, ?, ?)",
                (proposal_id, document_id, base_version, policy_id, policy_version, doc.content,
                 proposed_content, diff, reason.strip(), now(), config.safe_text(actor, 120),
                 json.dumps(provenance, sort_keys=True), run_id, finding_id, evidence or ''))
            lines = diff.splitlines()[2:]
            store.add_event(conn, "proposal.created", actor=actor,
                            subject=("proposal", proposal_id), metadata={
                                **meta, "proposal_id": proposal_id, "run_id": run_id,
                                "lines_added": sum(ln.startswith("+") for ln in lines),
                                "lines_removed": sum(ln.startswith("-") for ln in lines),
                                "original_sha256": config.fingerprint(doc.content),
                                "proposed_sha256": config.fingerprint(proposed_content),
                                "review_flags": flags})
            return store.proposal(proposal_id, conn)  # type: ignore[return-value]
    except WorkflowError as exc:
        if _conn is None:
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
            if new_status != 'rejected' and doc is not None:
                require_automatic_correction_allowed(doc)
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
                import autonomy
                if autonomy.policy(store,doc.channel,conn)['effective_mode']=='pull_request':
                    raise GuardrailViolation('Website corrections require the human-approved GitHub PR workflow')
                # Human review still revalidates exact content guardrails at write time.
                _, stale = _policy_context(store,proposal.policy_id,conn)
                violations,_=check_guardrails(proposal.original_content,proposal.proposed_content,
                    current_days=policy.voluntary_return_days,stale=stale,evidence=proposal.evidence,
                    effective_from=policy.effective_from)
                if violations:raise GuardrailViolation('Approved patch no longer passes content guardrails',violations=violations)
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


# ---- authenticated runs, retrievals and findings ----------------------------------------

RUN_TTL = timedelta(minutes=30)
RUN_LEASE = timedelta(seconds=90)
MAX_EVIDENCE = 1_000
MAX_FINDING_REASON = 1_000
RUN_ID_RE = re.compile(r"^run-[0-9a-f]{16}$")


def _normalize(text: str) -> str:
    return " ".join(text.split())


def open_run(store: Store, policy_id: str, document_ids: list[str], *, actor: str,
             agent_id: str | None = None, ttl: timedelta = RUN_TTL,
             metadata: dict[str, Any] | None = None,
             _conn: sqlite3.Connection | None = None) -> tuple[AgentRun, str]:
    """Only trusted local orchestration mints these short-lived capabilities."""
    if not document_ids or len(set(document_ids)) != len(document_ids):
        raise WorkflowError("document_ids must be a non-empty list without duplicates")
    if not timedelta(0) < ttl <= RUN_TTL:
        raise WorkflowError("run lifetime must be positive and at most 30 minutes")
    if _conn is None:
        store.reap_runs()
    run_id, token = f"run-{secrets.token_hex(8)}", "urt_" + secrets.token_urlsafe(32)
    created = datetime.now(UTC)
    with store.write(_conn) as conn:
        policy = store.policy(policy_id, conn=conn)
        if policy is None:
            raise NotFound("unknown policy")
        docs = [store.document(d, conn=conn) for d in document_ids]
        if any(d is None for d in docs):
            raise NotFound("run contains an unknown document")
        versions = {d.id: d.version for d in docs if d}
        conn.execute(
            "INSERT INTO agent_runs (id, policy_id, policy_version, document_ids, token_hash, "
            "status, created_at, expires_at, agent_id, document_versions, lease_until, metadata) "
            "VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?)",
            (run_id, policy_id, policy.version, json.dumps(document_ids), _sha(token),
             created.isoformat(), (created + ttl).isoformat(), agent_id, json.dumps(versions),
             (created + min(ttl, RUN_LEASE)).isoformat(),
             json.dumps(config.safe_metadata(metadata or {}))))
        import autonomy
        autonomy.pin_run(store,run_id,docs,conn)
        store.add_event(conn, "agent.run_started", actor=actor, subject=("run", run_id),
                        metadata={**(metadata or {}), "run_id": run_id, "agent_id": agent_id,
                                  "policy_id": policy_id, "policy_version": policy.version,
                                  "document_ids": document_ids})
        return store.run(run_id, conn), token


def _open_run(store: Store, run_id: str, conn: sqlite3.Connection) -> AgentRun:
    run = store.run(run_id, conn)
    if run is None:
        raise NotFound("unknown run")
    if run.status != "open":
        raise Conflict(f"run is {run.status}; it no longer accepts results")
    if datetime.now(UTC) >= run.expires_at:
        raise Conflict("run has expired")
    if datetime.now(UTC) >= run.lease_until:
        raise Conflict("runner heartbeat stopped")
    policy = store.policy(run.policy_id, conn=conn)
    if policy is None or policy.version != run.policy_version:
        raise Conflict("policy version changed during the run")
    return run


def authorize_run(store: Store, run_id: str, run_token: str,
                  conn: sqlite3.Connection) -> AgentRun:
    if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
        raise Unauthorized("invalid run credentials")
    row = conn.execute("SELECT token_hash FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
    if (row is None or not isinstance(run_token, str) or not row['token_hash']
            or not hmac.compare_digest(_sha(run_token), row['token_hash'])):
        raise Unauthorized("invalid run credentials")
    return _open_run(store, run_id, conn)


def heartbeat_run(store: Store, run_id: str, *, provenance: dict[str, Any] | None = None) -> None:
    """Runner-only lease renewal. MCP clients cannot renew or bind external identities."""
    with store.write() as conn:
        run = _open_run(store, run_id, conn)
        refs = provenance or {}
        conn.execute("UPDATE agent_runs SET lease_until = ?, thread_id = COALESCE(?, thread_id), "
                     "message_id = COALESCE(?, message_id) WHERE id = ?",
                     (min(run.expires_at, datetime.now(UTC) + RUN_LEASE).isoformat(),
                      refs.get('thread_id'), refs.get('message_id'), run_id))


def close_run(store: Store, run_id: str, *, status: str, actor: str,
              provenance: dict[str, Any] | None = None, metadata: dict[str, Any] | None = None,
              error: str | None = None, _conn: sqlite3.Connection | None = None) -> AgentRun:
    # The first terminal transition wins; cleanup/retries must not rewrite it.
    if status not in {'completed', 'incomplete', 'failed', 'expired', 'interrupted'}:
        raise WorkflowError("invalid terminal run status")
    with store.write(_conn) as conn:
        run = store.run(run_id, conn)
        if run is None:
            raise NotFound("unknown run")
        if run.status != 'open':
            return run
        refs = provenance or {}
        combined = config.safe_metadata({**run.metadata, **(metadata or {})})
        conn.execute("UPDATE agent_runs SET status = ?, closed_at = ?, "
                     "thread_id = COALESCE(?, thread_id), message_id = COALESCE(?, message_id), "
                     "metadata = ?, error = ? WHERE id = ?",
                     (status, now(), refs.get('thread_id'), refs.get('message_id'),
                      json.dumps(combined), config.safe_text(error, 500) if error else None, run_id))
        findings = store.findings(run_id=run_id, conn=conn)
        store.add_event(conn, 'agent.run_finished', actor=actor,
                        status='ok' if status == 'completed' else 'error', subject=('run', run_id),
                        metadata={**combined, **refs, 'run_id': run_id, 'run_status': status,
                                  'findings_recorded': len(findings),
                                  'documents_without_finding': sorted(set(run.document_ids)
                                      - {f.document_id for f in findings})}, error=error)
        return store.run(run_id, conn)


def retrieve_for_run(store: Store, *, run_id: str, run_token: str,
                     kind: str, subject_id: str, actor: str):
    """Authenticate and record the exact version served, in the same transaction.

    Unscoped MCP reads are intentionally never evidence for any run. Tool failures
    are recorded with version 0, allowing an honest UNKNOWN with no document version.
    """
    store.reap_runs()
    failure = None
    with store.write() as conn:
        run = authorize_run(store, run_id, run_token, conn)
        if kind == 'policy':
            if subject_id != run.policy_id:
                raise WorkflowError("policy is outside the run scope")
            value = store.policy(subject_id, conn=conn)
            expected = run.policy_version
        elif kind == 'document':
            if subject_id not in run.document_ids:
                raise WorkflowError("document is outside the run scope")
            value = store.document(subject_id, conn=conn)
            expected = run.document_versions.get(subject_id)
        else:
            raise WorkflowError("invalid retrieval kind")
        if value is None:
            failure = 'document retrieval failed' if kind == 'document' else 'policy retrieval failed'
        elif value.version != expected:
            failure = 'version changed during the run'
        version = value.version if value is not None and not failure else 0
        conn.execute("INSERT INTO run_retrievals VALUES (?, ?, ?, ?, ?, ?, ?) "
                     "ON CONFLICT(run_id, kind, subject_id, version) DO NOTHING",
                     (run_id, kind, subject_id, version, 'error' if failure else 'ok', failure, now()))
        store.add_event(conn, 'agent.retrieval', actor=actor,
                        status='error' if failure else 'ok', subject=(kind, subject_id),
                        metadata={'run_id': run_id, 'kind': kind, 'version': version}, error=failure)
    if failure:
        raise Conflict(failure)
    return value


def retrieval_status(store: Store, run: AgentRun,
                     conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    with store.read(conn) as c:
        rows = c.execute("SELECT * FROM run_retrievals WHERE run_id = ? AND status = 'ok'",
                         (run.id,)).fetchall()
        policy = sorted({r['version'] for r in rows if r['kind'] == 'policy'
                         and r['subject_id'] == run.policy_id and r['version'] == run.policy_version})
        docs = {d: sorted({r['version'] for r in rows if r['kind'] == 'document'
                          and r['subject_id'] == d and r['version'] == run.document_versions.get(d)})
                for d in run.document_ids}
    missing = ([] if policy else [f'policy:{run.policy_id}']) + [d for d, v in docs.items() if not v]
    return {'policy_versions': policy, 'document_versions': docs,
            'missing': missing, 'complete': not missing}


def _validate_finding(store: Store, conn: sqlite3.Connection, run: AgentRun, *,
                      policy_id: str, policy_version: int | None, document_id: str,
                      document_version: int | None, classification: str,
                      evidence: str, reason: str, proposed_content: str | None = None) -> bool:
    if not isinstance(classification, str) or classification not in CATEGORIES:
        raise WorkflowError("invalid classification category")
    if proposed_content is not None and (classification != 'OUTDATED' or not isinstance(proposed_content, str)
            or not proposed_content.strip() or len(proposed_content) > MAX_CONTENT):
        raise WorkflowError('corrections are optional, non-empty text for OUTDATED findings only')
    if proposed_content is not None and config.redact(proposed_content) != proposed_content:
        raise WorkflowError('credentials are forbidden in correction text')
    if policy_id != run.policy_id or not (
            (policy_version is None and classification == 'UNKNOWN')
            or (type(policy_version) is int and policy_version == run.policy_version)):
        raise Conflict("finding policy must match the run's policy version")
    if document_id not in run.document_ids:
        raise WorkflowError("document is outside the run scope")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > MAX_FINDING_REASON:
        raise WorkflowError("finding reason must be 1-1000 characters")
    if not isinstance(evidence, str) or len(evidence) > MAX_EVIDENCE:
        raise WorkflowError("finding evidence must be a string of at most 1000 characters")
    if config.redact(reason) != reason or config.redact(evidence) != evidence:
        raise WorkflowError("credentials are forbidden in finding text")
    if document_version is not None and (type(document_version) is not int or document_version < 1):
        raise WorkflowError("document_version must be a positive integer or null for UNKNOWN")
    retrieval = retrieval_status(store, run, conn)
    doc_read = bool(retrieval['document_versions'][document_id])
    if classification == 'UNKNOWN' and document_version is None:
        if evidence:
            raise WorkflowError("UNKNOWN without a retrieved version cannot quote evidence")
        # A response can be lost after the server served it. UNKNOWN is never
        # publication evidence, so do not force the agent to invent that version.
        return False
    doc = store.document(document_id, conn=conn)
    if doc is None or document_version != doc.version or document_version != run.document_versions.get(document_id):
        raise Conflict("stale document version")
    verified = doc_read and bool(retrieval['policy_versions'])
    if not doc_read or (classification != 'UNKNOWN' and not verified):
        raise WorkflowError("authenticated policy and document retrieval is required")
    if not evidence or evidence not in doc.content:
        raise WorkflowError("evidence must be an exact quote from the retrieved document")
    if proposed_content is not None:
        require_automatic_correction_allowed(doc)
        policy, stale = _policy_context(store, run.policy_id, conn)
        violations, flags = check_guardrails(doc.content, proposed_content,
            current_days=policy.voluntary_return_days, stale=stale, evidence=evidence,
            effective_from=policy.effective_from)
        if violations:
            raise GuardrailViolation('correction violates content guardrails', violations=violations, review_flags=flags)
    return verified


def _save_finding(store: Store, conn: sqlite3.Connection, run: AgentRun, *,
                  document_id: str, document_version: int | None, classification: str,
                  evidence: str, reason: str, actor: str, source: str,
                  proposed_content: str | None = None) -> tuple[Finding, bool]:
    verified = _validate_finding(store, conn, run, policy_id=run.policy_id,
        policy_version=run.policy_version, document_id=document_id, document_version=document_version,
        classification=classification, evidence=evidence, reason=reason, proposed_content=proposed_content)
    values = [run.policy_version, document_version, classification, evidence, reason]
    if proposed_content is not None:
        values.append(proposed_content)
    submission = _sha(json.dumps(values))
    existing = conn.execute("SELECT * FROM findings WHERE run_id = ? AND document_id = ?",
                            (run.id, document_id)).fetchone()
    meta = {'run_id': run.id, 'document_id': document_id, 'document_version': document_version,
            'policy_id': run.policy_id, 'policy_version': run.policy_version, 'category': classification}
    if existing:
        if existing['submission_sha256'] != submission:
            raise Conflict("a different finding was already recorded for this document in this run")
        return store.findings(run_id=run.id, document_id=document_id, conn=conn)[0], True
    fid = f'fnd-{secrets.token_hex(8)}'
    conn.execute("INSERT INTO findings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (fid, run.id, run.policy_id, run.policy_version, document_id, document_version,
                  classification, evidence, reason.strip(), int(verified), submission, now(), source))
    if proposed_content is not None:
        conn.execute('INSERT INTO finding_corrections VALUES (?,?)', (fid, proposed_content))
    store.add_event(conn, 'finding.recorded', actor=actor, subject=('document', document_id),
                    metadata={**meta, 'finding_id': fid, 'retrieved_during_run': verified, 'source': source})
    return store.findings(run_id=run.id, document_id=document_id, conn=conn)[0], False


def record_finding(store: Store, *, run_id: str, run_token: str, policy_id: str,
                   policy_version: int | None, document_id: str, document_version: int | None,
                   classification: str, evidence: str, reason: str,
                   actor: str, proposed_content: str | None = None) -> tuple[Finding, bool]:
    store.reap_runs()
    authenticated = False
    try:
        with store.write() as conn:
            run = authorize_run(store, run_id, run_token, conn)
            authenticated = True
            _validate_finding(store, conn, run, policy_id=policy_id, policy_version=policy_version,
                document_id=document_id, document_version=document_version, classification=classification,
                evidence=evidence, reason=reason, proposed_content=proposed_content)
            return _save_finding(store, conn, run, document_id=document_id,
                document_version=document_version, classification=classification,
                evidence=evidence, reason=reason, actor=actor, source='mcp', proposed_content=proposed_content)
    except WorkflowError as exc:
        # Never persist untrusted ids or credentials from rejected submissions.
        store.record_event('finding.rejected', actor=actor,
                           status='denied' if isinstance(exc, Unauthorized) else 'error',
                           metadata={'run_id': run_id if authenticated else None},
                           error=type(exc).__name__ + ': ' + config.redact(
                               str(exc), run_token if isinstance(run_token, str) else None))
        raise


def ingest_agent_reply(store: Store, reply: str, *, actor: str,
                       provenance: dict[str, Any], run_id: str | None = None,
                       execution_status: str | None = None,
                       metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """Trusted runner finalization: validate the whole batch before any proposals.

    Live findings survive rejected final output. Final JSON can fill missing findings,
    but must pass exactly the same scope, version, quote and retrieval checks. Discovery
    isolates honest UNKNOWNs after successful whole-batch reconciliation; malformed or
    contradictory replies still roll back every new proposal and certification.
    """
    summary: dict[str, Any] = {'classifications': {}, 'proposals': [], 'errors': []}
    if not run_id:
        summary['errors'].append({'error': 'an authenticated analysis run is required'})
        return summary
    store.reap_runs()
    run = store.run(run_id)
    if run is None or run.status != 'open':
        summary['errors'].append({'error': 'run is not open'})
        return summary
    provenance = {**config.safe_metadata(provenance), 'run_id': run_id}
    if execution_status != 'success':
        close_run(store, run_id, status='interrupted' if execution_status == 'cancelled' else 'failed',
                  actor=actor, provenance=provenance, metadata=metadata, error='agent execution did not succeed')
        summary['errors'].append({'error': 'agent execution did not succeed'})
        return summary
    document_id = None
    try:
        with store.write() as conn:
            run = _open_run(store, run_id, conn)
            verdict = parse_verdict(reply)
            if verdict is None:
                raise WorkflowError('no JSON verdict block')
            entries = verdict.get('documents')
            if not isinstance(entries, dict) or set(entries) != set(run.document_ids):
                raise WorkflowError('final document set must exactly match the run scope')
            all_unknown = all(isinstance(entry, dict) and entry.get('category') == 'UNKNOWN'
                              for entry in entries.values())
            policy = verdict.get('policy')
            if (not isinstance(policy, dict) or policy.get('id') != run.policy_id
                    or not ((all_unknown and policy.get('version') is None)
                        or (type(policy.get('version')) is int and policy['version'] == run.policy_version))):
                raise Conflict('policy version mismatch')
            current = store.policy(run.policy_id, conn=conn)
            if not ((all_unknown and policy.get('voluntary_return_days') is None)
                    or (type(policy.get('voluntary_return_days')) is int
                        and policy['voluntary_return_days'] == current.voluntary_return_days)):
                raise WorkflowError('reported policy facts do not match the authoritative policy')
            if verdict.get('changes_made') is not False:
                raise WorkflowError('agent must report changes_made=false')
            recorded = {f.document_id: f for f in store.findings(run_id=run_id, conn=conn)}
            for did, entry in entries.items():
                document_id = did
                if not isinstance(entry, dict):
                    raise WorkflowError('invalid document finding')
                category, version = entry.get('category'), entry.get('document_version')
                evidence, reason = entry.get('evidence'), entry.get('reason')
                _validate_finding(store, conn, run, policy_id=run.policy_id,
                    policy_version=run.policy_version, document_id=did, document_version=version,
                    classification=category, evidence=evidence, reason=reason,
                    proposed_content=entry.get('proposed_content') if run.metadata.get('discovery') else None)
                existing = recorded.get(did)
                if existing and (existing.category, existing.document_version, existing.evidence, existing.reason) != (
                        category, version, evidence, reason.strip()):
                    raise Conflict('final reply contradicts the recorded finding')
                if existing and existing.proposed_content is not None:
                    if entry.get('proposed_content') not in (None, existing.proposed_content):
                        raise Conflict('final reply contradicts the recorded correction')
                    entry['proposed_content'] = existing.proposed_content
                if category == 'OUTDATED' and (not isinstance(entry.get('proposed_content'), str)
                                             or not entry['proposed_content'].strip()) and not run.metadata.get('discovery'):
                    raise WorkflowError('OUTDATED finding requires a correction')
            # All findings are valid before inserting compatibility fallback rows.
            for did, entry in entries.items():
                document_id = did
                if did not in recorded:
                    recorded[did], _ = _save_finding(store, conn, run, document_id=did,
                        document_version=entry.get('document_version'), classification=entry['category'],
                        evidence=entry['evidence'], reason=entry['reason'], actor=actor, source='final_json',
                        proposed_content=entry.get('proposed_content') if run.metadata.get('discovery') else None)
            retrieval = retrieval_status(store, run, conn)
            complete = retrieval['complete'] and all(f.category != 'UNKNOWN' for f in recorded.values())
            final_metadata = {**(metadata or {}), 'retrieval': retrieval,'reconciliation':'verified_reply_v1'}
            if run.metadata.get('discovery'):
                from discovery import validate_search_strategy
                strategy = validate_search_strategy(store, run, conn)
                outcomes, verified_ids = {}, []
                for did, entry in entries.items():
                    document_id = did
                    finding = recorded[did]
                    verified = finding.retrieved_during_run and finding.category != 'UNKNOWN'
                    if verified:
                        verified_ids.append(did)
                    doc = store.document(did, conn=conn)
                    if not verified:
                        action = 'needs_clarification'
                    elif finding.category != 'OUTDATED':
                        action = 'no_change'
                    elif doc.channel in MANUAL_ONLY_CHANNELS:
                        action = 'manual_only'
                    elif not entry.get('proposed_content'):
                        action = 'needs_correction'
                    else:
                        action = 'proposed'
                    outcomes[did] = {'action': action}
                    if action != 'proposed':
                        continue
                    p = submit_proposal(store, document_id=did, base_version=entry['document_version'],
                        policy_id=run.policy_id, policy_version=run.policy_version,
                        proposed_content=entry['proposed_content'], reason=entry['reason'], actor=actor,
                        evidence=entry['evidence'], provenance=provenance,
                        run_id=run_id, finding_id=finding.id, _conn=conn)
                    summary['proposals'].append(p.id)
                    outcomes[did]['proposal_id'] = p.id
                pending = [did for did, outcome in outcomes.items()
                           if outcome['action'] not in {'proposed', 'no_change'}]
                complete = retrieval['complete'] and not pending
                summary['document_outcomes'] = outcomes
                final_metadata.update(finalization='per_document_v1', verified_document_ids=verified_ids,
                                      document_outcomes=outcomes, search_strategy=strategy)
                if pending:
                    summary['errors'].append({'error': 'documents require clarification or manual correction',
                                              'document_ids': pending})
            elif complete:
                for did, entry in entries.items():
                    document_id = did
                    if entry['category'] != 'OUTDATED' or not entry.get('proposed_content'):
                        continue
                    p = submit_proposal(store, document_id=did, base_version=entry['document_version'],
                        policy_id=run.policy_id, policy_version=run.policy_version,
                        proposed_content=entry['proposed_content'], reason=entry['reason'], actor=actor,
                        evidence=entry['evidence'], provenance=provenance,
                        run_id=run_id, finding_id=recorded[did].id, _conn=conn)
                    summary['proposals'].append(p.id)
            else:
                summary['errors'].append({'error': 'analysis has UNKNOWN findings or incomplete retrieval'})
            summary['classifications'] = {d: f.category for d, f in recorded.items()}
            close_run(store, run_id, status='completed' if complete else 'incomplete', actor=actor,
                      provenance=provenance, metadata=final_metadata,
                      error=None if complete else summary['errors'][0]['error'], _conn=conn)
            import autonomy
            applied=autonomy.apply_pending(store,run_id,conn)
            if applied:
                summary['auto_applications']=applied
                # This is local mutation evidence, not a replacement agent classification.
                current_run=store.run(run_id,conn)
                updated={**current_run.metadata,'auto_applications':applied}
                conn.execute('UPDATE agent_runs SET metadata=? WHERE id=?',(json.dumps(config.safe_metadata(updated)),run_id))
    except WorkflowError as exc:
        diagnostic = config.safe_metadata({'error': str(exc), 'code': exc.code,
            **({'document_id': document_id} if document_id else {}),
            **({'details': exc.details} if exc.details else {})})
        summary = {'classifications': {}, 'proposals': [], 'errors': [diagnostic]}
        store.record_event('agent.reply_rejected', actor=actor, status='error', subject=('run', run_id),
                           metadata={'run_id': run_id, **diagnostic}, error=str(exc))
        current_run = store.run(run_id)
        if current_run and current_run.status == 'open':
            from discovery import SearchStrategyViolation
            strategy_failure = isinstance(exc, SearchStrategyViolation)
            failure_metadata = {**(metadata or {}), 'failure_diagnostics': diagnostic}
            if strategy_failure:
                failure_metadata.update(search_strategy=exc.details['observed'], strategy_validation={
                    'status': 'failed', 'code': exc.code, 'message': str(exc), 'details': exc.details})
            close_run(store, run_id, status='failed' if strategy_failure else 'incomplete', actor=actor, provenance=provenance,
                      metadata=failure_metadata, error=str(exc))
    return summary
