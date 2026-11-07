"""Undrift Guard: fail-closed validation of outgoing customer-facing policy text.

A separate Cominty Managed Agent ("Undrift Guard") judges a candidate response against a case
that Undrift assembles from authoritative data: the current policy, chosen document evidence
and reviewer-supplied facts. The agent has no tools; the verified case is its only evidence.

Nothing here sends a message, publishes, approves or applies anything. The backend
independently verifies every agent output and combines it with a deterministic precheck that
can only escalate. Missing configuration, execution failures, timeouts, malformed or
contradictory output and stale versions all fail closed (NEEDS_REVIEW or BLOCK). Only a
verified Cominty ALLOW with a clear precheck on a non-protected channel is release-eligible,
and release eligibility is a validation result, not a permission to send.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import config
import workflow as w
from store import Store, now

INPUT_CONTRACT = 'undrift.guard.input.v1'
OUTPUT_CONTRACT = 'undrift.guard.output.v1'
POLICY_ID = 'shiftly-returns'
INSTRUCTIONS = config.ROOT / 'agents' / 'guard.md'
VERDICTS = ('ALLOW', 'NEEDS_REVIEW', 'BLOCK')
RANK = {v: i for i, v in enumerate(VERDICTS)}
ASSESSMENTS = {'SUPPORTED', 'CONTRADICTS_POLICY', 'REVOKES_COMMITMENT', 'MISSTATES_STATUTORY',
               'UNVERIFIED_SPECIFIC', 'INSUFFICIENT_EVIDENCE'}
BLOCKING = {'CONTRADICTS_POLICY', 'REVOKES_COMMITMENT', 'MISSTATES_STATUTORY'}
STATUTORY_DAYS = 14
MAX_CANDIDATE, MAX_FACTS, MAX_FACT, MAX_EVIDENCE, MAX_REASON = 2000, 10, 300, 8, 1000
TIMEOUT_S = 120

_DAYS = re.compile(r'\b(\d{1,3})\s*-?\s*(?:calendar\s+)?days?\b', re.I)
_REVOKED = re.compile(r"\b(no longer|(?:does|do|is|are|will)(?:n't| not) (?:apply|applies|valid|available|honou?red)|"
                      r"not (?:apply|valid|available|honou?red)|revoked?|cancel(?:l?ed)?|expired?|void|invalid|"
                      r"removed|ended|ceased?)\b", re.I)
_STATUTORY = re.compile(r'\b(statutory|withdrawal|right of withdrawal|cooling[- ]off)\b', re.I)
_COMMITMENT = re.compile(r'\b(previously promised|promised|original terms|(?:placed|made|purchased|bought) before|'
                         r'your (?:earlier|previous|original))\b', re.I)
_ORDER = re.compile(r'\border\s*#?\s*(\d{3,})', re.I)
_NUMBER_WORDS = {w: i for i, w in enumerate('one two three four five six seven eight nine ten eleven twelve'.split(), 1)} | {'a': 1, 'an': 1}
_SPELLED = re.compile(r'\b(\d{1,3}|' + '|'.join(_NUMBER_WORDS) + r')[\s-]+(weeks?|months?|years?)\b', re.I)
_UNIT_DAYS = {'week': (7, True), 'month': (30, False), 'year': (365, False)}  # (days, exact)
_PROMO = re.compile(r'\b(promotion|promo|special offer|extended return|exception(?:al(?:ly)?)?|goodwill)\b', re.I)
_PII = [(re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+'), '<email>'),
        (re.compile(r'\+?\d[\d ()/-]{7,}\d'), '<number>'),
        (re.compile(r'\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}\b'), '<iban>')]

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS guard_checks (
        id TEXT PRIMARY KEY, database_id TEXT NOT NULL, created_at TEXT NOT NULL,
        requested_by TEXT NOT NULL, input_contract TEXT NOT NULL, output_contract TEXT NOT NULL,
        channel_id TEXT NOT NULL, candidate_sha256 TEXT NOT NULL, candidate_length INTEGER NOT NULL,
        candidate_redacted TEXT NOT NULL, case_sha256 TEXT NOT NULL, policy_id TEXT NOT NULL,
        policy_version INTEGER NOT NULL, evidence TEXT NOT NULL, facts TEXT NOT NULL,
        precheck TEXT NOT NULL, execution TEXT NOT NULL, agent_output TEXT,
        verification TEXT NOT NULL,
        decision TEXT NOT NULL CHECK (decision IN ('ALLOW','NEEDS_REVIEW','BLOCK')),
        decision_source TEXT NOT NULL, release_eligible INTEGER NOT NULL CHECK (release_eligible IN (0,1)),
        reasons TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS guard_checks_by_db ON guard_checks(database_id, created_at)",
    """CREATE TRIGGER IF NOT EXISTS guard_checks_immutable_update BEFORE UPDATE ON guard_checks
        BEGIN SELECT RAISE(ABORT, 'guard audit records are immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS guard_checks_immutable_delete BEFORE DELETE ON guard_checks
        BEGIN SELECT RAISE(ABORT, 'guard audit records are immutable'); END""",
)
JSON_FIELDS = ('evidence', 'facts', 'precheck', 'execution', 'agent_output', 'verification', 'reasons')


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _spelled_periods(sentence: str) -> dict[str, tuple[int, bool]]:
    """Periods stated in weeks, months or years as (day equivalent, exact). Months and years are approximate."""
    out = {}
    for m in _SPELLED.finditer(sentence):
        count = int(m.group(1)) if m.group(1).isdigit() else _NUMBER_WORDS[m.group(1).lower()]
        per_unit, exact = _UNIT_DAYS[m.group(2).lower().rstrip('s')]
        out[m.group(0)] = (count * per_unit, exact)
    return out


def redact_pii(text: str) -> str:
    text = config.redact(text)
    for rx, repl in _PII:
        text = rx.sub(repl, text)
    return text


class GuardJournal:
    """Separate, append-only audit store; operational and historical databases are not migrated."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        for statement in SCHEMA:
            conn.execute(statement)
        return conn

    def record(self, row: dict[str, Any]) -> None:
        conn = self.connect()
        try:
            values = {k: (json.dumps(v, sort_keys=True) if k in JSON_FIELDS and v is not None else v)
                      for k, v in row.items()}
            values['release_eligible'] = int(bool(row['release_eligible']))
            cols = ', '.join(values)
            conn.execute(f'INSERT INTO guard_checks ({cols}) VALUES ({", ".join("?" * len(values))})',
                         list(values.values()))
        finally:
            conn.close()

    @staticmethod
    def _row(r: sqlite3.Row) -> dict[str, Any]:
        out = dict(r)
        for k in JSON_FIELDS:
            out[k] = json.loads(out[k]) if out[k] is not None else None
        out['release_eligible'] = bool(out['release_eligible'])
        return out

    def get(self, check_id: str, database_id: str) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        conn = self.connect()
        try:
            r = conn.execute('SELECT * FROM guard_checks WHERE id=? AND database_id=?',
                             (check_id, database_id)).fetchone()
            return self._row(r) if r else None
        finally:
            conn.close()

    def list(self, database_id: str, limit: int = 50) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        conn = self.connect()
        try:
            return [self._row(r) for r in conn.execute(
                'SELECT * FROM guard_checks WHERE database_id=? ORDER BY created_at DESC LIMIT ?',
                (database_id, max(1, min(limit, 200))))]
        finally:
            conn.close()


# ---- case ---------------------------------------------------------------------------------

def _superseded_days(store: Store, policy: Any, conn: sqlite3.Connection) -> set[int]:
    older = {r[0] for r in conn.execute(
        'SELECT voluntary_return_days FROM policy_versions WHERE policy_id=? AND version<?',
        (policy.id, policy.version))}
    return older - {policy.voluntary_return_days}


def build_case(store: Store, *, candidate: Any, channel_id: Any, evidence_document_ids: Any = (),
               facts: Any = (), requested_by: str, policy_id: str = POLICY_ID) -> dict[str, Any]:
    """Assemble the verified input from authoritative data; reject malformed requests."""
    if not isinstance(candidate, str) or not candidate.strip() or len(candidate) > MAX_CANDIDATE:
        raise w.WorkflowError(f'candidate must be 1-{MAX_CANDIDATE} characters')
    if not isinstance(evidence_document_ids, (list, tuple)) or len(evidence_document_ids) > MAX_EVIDENCE \
            or len(set(evidence_document_ids)) != len(evidence_document_ids) \
            or not all(isinstance(d, str) for d in evidence_document_ids):
        raise w.WorkflowError(f'evidence_document_ids must be up to {MAX_EVIDENCE} distinct ids')
    if not isinstance(facts, (list, tuple)) or len(facts) > MAX_FACTS \
            or not all(isinstance(f, str) and f.strip() and len(f) <= MAX_FACT for f in facts):
        raise w.WorkflowError(f'facts must be up to {MAX_FACTS} statements of 1-{MAX_FACT} characters')
    text = candidate.strip()
    with store.read() as c:
        channels = {ch['id'] for ch in store.channels(c)}
        if channel_id not in channels:
            raise w.NotFound(f'unknown channel {channel_id!r}')
        policy = store.policy(policy_id, conn=c)
        if policy is None:
            raise w.NotFound(f'unknown policy {policy_id!r}')
        evidence = []
        for i, did in enumerate(evidence_document_ids, 1):
            doc = store.document(did, conn=c)
            if doc is None:
                raise w.NotFound(f'unknown document {did!r}')
            evidence.append({'ref': f'E{i}', 'document_id': doc.id, 'channel_id': doc.channel,
                             'version': doc.version, 'sha256': sha256(doc.content), 'content': doc.content})
        superseded = sorted(_superseded_days(store, policy, c))
    case = {
        'contract': INPUT_CONTRACT,
        'check_id': f'gchk-{secrets.token_hex(8)}',
        'candidate': {'text': text, 'sha256': sha256(text)},
        'channel_id': channel_id,
        'policy': {'ref': 'P', 'id': policy.id, 'version': policy.version,
                   'voluntary_return_days': policy.voluntary_return_days,
                   'effective_from': policy.effective_from.isoformat(), 'description': policy.description,
                   'superseded_voluntary_return_days': superseded, 'statutory_withdrawal_days': STATUTORY_DAYS},
        'evidence': evidence,
        'facts': [{'ref': f'F{i}', 'statement': f.strip(), 'source': f'reviewer:{requested_by}'}
                  for i, f in enumerate(facts, 1)],
        'request': {'requested_by': requested_by, 'requested_at': now()},
    }
    case['case_sha256'] = sha256(json.dumps({k: v for k, v in case.items() if k != 'request'}, sort_keys=True))
    return case


def allowed_refs(case: dict[str, Any]) -> set[str]:
    return {'P'} | {e['ref'] for e in case['evidence']} | {f['ref'] for f in case['facts']}


# ---- deterministic precheck (can only escalate) -------------------------------------------

def precheck(case: dict[str, Any]) -> dict[str, Any]:
    policy = case['policy']
    superseded = set(policy['superseded_voluntary_return_days'])
    allowed = {policy['voluntary_return_days'], STATUTORY_DAYS} | superseded
    fact_text = ' '.join(f['statement'] for f in case['facts'])
    findings: list[dict[str, Any]] = []

    def add(rule: str, verdict: str, sentence: str, reason: str) -> None:
        findings.append({'rule': rule, 'verdict': verdict, 'sentence': sentence, 'reason': reason})

    for sentence in w.split_sentences(case['candidate']['text']):
        days = {int(d) for d in _DAYS.findall(sentence)}
        spelled = _spelled_periods(sentence)
        negated = bool(_REVOKED.search(sentence))
        if _STATUTORY.search(sentence) and (negated or days - {STATUTORY_DAYS}
                                            or {d for d, _ in spelled.values()} - {STATUTORY_DAYS}):
            add('statutory_right', 'BLOCK', sentence,
                f'Statutory {STATUTORY_DAYS}-day withdrawal rights must not be revoked or restated differently.')
            continue
        commitment = bool(_COMMITMENT.search(sentence))
        if commitment and negated:
            add('existing_commitment', 'BLOCK', sentence,
                'Purchase-time commitments made before the change remain protected.')
        elif days & superseded and not commitment:
            add('superseded_period', 'BLOCK', sentence,
                f'States a superseded period ({", ".join(map(str, sorted(days & superseded)))} days) '
                f'as current; policy v{policy["version"]} sets {policy["voluntary_return_days"]} days.')
        # Weeks/months/years bypass the day-based rules above; escalate them, never clear them.
        for phrase, (equivalent, exact) in spelled.items():
            if equivalent in superseded and not commitment:
                add('superseded_period', 'BLOCK', sentence,
                    f'"{phrase}" is about {equivalent} days, a superseded period; policy v{policy["version"]} '
                    f'sets {policy["voluntary_return_days"]} days.')
            elif equivalent not in allowed or not exact:
                add('period_not_in_days', 'NEEDS_REVIEW', sentence,
                    f'"{phrase}" ({"=" if exact else "about"} {equivalent} days) does not state an exact policy period '
                    f'in days; the current policy sets {policy["voluntary_return_days"]} days.')
        for number in _ORDER.findall(sentence):
            if number not in fact_text:
                add('order_specific', 'NEEDS_REVIEW', sentence,
                    f'Order #{number}: no verified fact supports an order-specific eligibility claim.')
        if _PROMO.search(sentence) and not case['facts']:
            add('promotion_or_exception', 'NEEDS_REVIEW', sentence,
                'Promotions or exceptions need a verified supporting fact.')
        if days - allowed:
            add('unrecognized_period', 'NEEDS_REVIEW', sentence,
                f'Period {", ".join(map(str, sorted(days - allowed)))} days matches no policy version.')
    verdict = max((f['verdict'] for f in findings), key=RANK.__getitem__, default='CLEAR')
    return {'verdict': verdict, 'findings': findings}


# ---- agent output verification ------------------------------------------------------------

def verify_output(case: dict[str, Any], reply: str | None) -> tuple[str | None, dict | None, list[str]]:
    """Return (verdict, parsed_output, problems). Any problem means the verdict is not trusted."""
    if not isinstance(reply, str) or not reply.strip():
        return None, None, ['empty agent reply']
    out = w.parse_verdict(reply)
    if out is None:
        return None, None, ['no JSON output block']
    problems: list[str] = []
    text = case['candidate']['text']
    refs = allowed_refs(case)
    if out.get('contract') != OUTPUT_CONTRACT:
        problems.append('output contract mismatch')
    if out.get('check_id') != case['check_id']:
        problems.append('check_id does not match this request')
    if out.get('candidate_sha256') != case['candidate']['sha256']:
        problems.append('candidate hash does not match the exact candidate text')
    pol = out.get('policy') if isinstance(out.get('policy'), dict) else {}
    if (pol.get('id'), pol.get('version')) != (case['policy']['id'], case['policy']['version']):
        problems.append('policy binding does not match the verified policy version')
    verdict = out.get('verdict')
    if verdict not in VERDICTS:
        problems.append('verdict must be ALLOW, BLOCK or NEEDS_REVIEW')
    findings = out.get('findings')
    if not isinstance(findings, list) or not findings:
        problems.append('findings must be a non-empty list')
        findings = []
    clean = []
    for i, f in enumerate(findings, 1):
        if not isinstance(f, dict):
            problems.append(f'finding {i} is not an object')
            continue
        claim, assessment, cited, reason = f.get('claim'), f.get('assessment'), f.get('evidence_refs'), f.get('reason')
        if not isinstance(claim, str) or not claim.strip() or claim not in text:
            problems.append(f'finding {i}: claim is not an exact quote from the candidate')
        if assessment not in ASSESSMENTS:
            problems.append(f'finding {i}: unknown assessment')
        if not isinstance(cited, list) or not all(isinstance(r, str) for r in cited):
            problems.append(f'finding {i}: evidence_refs must be a list of references')
            cited = []
        elif set(cited) - refs:
            problems.append(f'finding {i}: cites evidence that was not supplied ({", ".join(sorted(set(cited) - refs))})')
        if not isinstance(reason, str) or not reason.strip() or len(reason) > MAX_REASON:
            problems.append(f'finding {i}: reason must be 1-{MAX_REASON} characters')
        clean.append({'claim': claim, 'assessment': assessment, 'evidence_refs': cited, 'reason': reason})
    uncertainty = out.get('uncertainty')
    if uncertainty is not None and not isinstance(uncertainty, str):
        problems.append('uncertainty must be text or null')
        uncertainty = None
    uncertain = bool(uncertainty and uncertainty.strip().lower() not in ('', 'none'))
    assessments = {f['assessment'] for f in clean}
    if verdict == 'ALLOW':
        if assessments != {'SUPPORTED'}:
            problems.append('contradictory output: ALLOW with an unsupported or problematic claim')
        if any(not f['evidence_refs'] for f in clean):
            problems.append('ALLOW requires evidence references for every claim')
        if uncertain:
            problems.append('contradictory output: ALLOW with stated uncertainty')
        supported = [f['claim'] for f in clean if f['assessment'] == 'SUPPORTED' and isinstance(f['claim'], str)]
        uncovered = [s for s in w.split_sentences(text) if not any(c in s or s in c for c in supported)]
        if uncovered:
            problems.append(f'ALLOW does not cover {len(uncovered)} candidate sentence(s)')
    elif verdict == 'BLOCK' and not assessments & BLOCKING:
        problems.append('contradictory output: BLOCK without a blocking finding')
    elif verdict == 'NEEDS_REVIEW' and assessments <= {'SUPPORTED'} and not uncertain:
        problems.append('contradictory output: NEEDS_REVIEW without an unresolved claim or uncertainty')
    parsed = {'verdict': verdict, 'findings': clean, 'uncertainty': uncertainty}
    return (None if problems else verdict), parsed, problems


# ---- execution ----------------------------------------------------------------------------

@dataclass
class Execution:
    status: str
    source: str
    reply: str | None = None
    identity: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class DisabledExecutor:
    kind, paid = 'disabled', False

    def execute(self, case: dict[str, Any]) -> Execution:
        return Execution('not_configured', 'none', error='Cominty Guard agent is not configured or not enabled')


def build_message(case: dict[str, Any]) -> str:
    payload = json.dumps({k: v for k, v in case.items() if k != 'request'}, indent=1, sort_keys=True)
    return ('Validate this candidate customer-facing response. The JSON case below was assembled and '
            'verified by Undrift; it is your only evidence. Reply with the output contract from your '
            f'instructions, using check_id {case["check_id"]}.\n```json\n{payload}\n```')


class ComintyExecutor:
    """Real Cominty Managed Agent execution with every capability disabled (no tools)."""
    kind, paid = 'cominty', True

    def __init__(self, agent_id: str, client_factory=None, timeout_s: float = TIMEOUT_S) -> None:
        self.agent_id, self.timeout_s = agent_id, timeout_s
        self.client_factory = client_factory

    def _factory(self):
        if self.client_factory:
            return self.client_factory()
        from cominty_sdk import AsyncCominty
        return AsyncCominty()

    async def _run(self, case: dict[str, Any], identity: dict[str, Any]) -> Execution:
        from cominty_sdk import (AgentCapabilities, ImageGenerationCapability, IndexedDocumentsCapability,
                                 McpCapability, MemoryCapability, MusicGenerationCapability, SkillsCapability,
                                 VideoGenerationCapability, WebCapability, events)
        async with self._factory() as client:
            run = await client.chat.start(
                agent_id=self.agent_id, message=build_message(case), name='undrift-guard', max_steps=2,
                thread_capabilities=AgentCapabilities(
                    mcp=McpCapability(activation='never'), web=WebCapability(activation='never'),
                    indexed_documents=IndexedDocumentsCapability(activation='never'),
                    skills=SkillsCapability(activation='never'),
                    image_generation=ImageGenerationCapability(activation='never'),
                    video_generation=VideoGenerationCapability(activation='never'),
                    music_generation=MusicGenerationCapability(activation='never'),
                    memory=MemoryCapability(activation='never')))
            identity.update(thread_id=str(run.thread.id), message_id=str(run.message_id))
            async with run:
                async for event in run:
                    if isinstance(event, events.ToolCall):
                        name = str(getattr(event.data, 'name', '?'))[:80]
                        if name not in identity['tool_calls']:
                            identity['tool_calls'].append(name)
                    elif isinstance(event, events.LlmStep):
                        model = str(getattr(event.data, 'model', '') or '')[:80]
                        if model and model not in identity['models']:
                            identity['models'].append(model)
                    elif isinstance(event, events.Result):
                        identity['cost'] = json.loads(event.data.cost.model_dump_json())
            message = await run.result()
            status = getattr(message.status, 'value', message.status)
            if status != 'success':
                return Execution('error', 'cominty', message.content, identity,
                                 f'Cominty execution status {status}')
            if identity['tool_calls']:
                return Execution('error', 'cominty', message.content, identity,
                                 'Guard attempted a tool call although it has no tools')
            return Execution('success', 'cominty', message.content or '', identity)

    def execute(self, case: dict[str, Any]) -> Execution:
        identity: dict[str, Any] = {'agent_id': self.agent_id, 'models': [], 'tool_calls': [], 'cost': None}
        started = time.monotonic()
        try:
            result = asyncio.run(asyncio.wait_for(self._run(case, identity), self.timeout_s))
        except (TimeoutError, asyncio.TimeoutError):
            result = Execution('timeout', 'cominty', identity=identity,
                               error=f'Cominty Guard did not finish within {self.timeout_s:.0f} s')
        except Exception as exc:  # any SDK, network or service failure fails closed
            result = Execution('error', 'cominty', identity=identity,
                               error=f'Cominty Guard execution failed: {type(exc).__name__}')
        identity['duration_s'] = round(time.monotonic() - started, 2)
        return result


def executor_from_env():
    """Real execution needs an explicit agent id AND an explicit live switch; default is disabled."""
    agent_id = os.environ.get('UNDRIFT_GUARD_AGENT_ID', '').strip()
    if os.environ.get('UNDRIFT_GUARD_LIVE', '0').strip() == '1' and agent_id:
        return ComintyExecutor(agent_id)
    return DisabledExecutor()


def executor_status(executor) -> dict[str, Any]:
    return {'mode': executor.kind, 'paid': executor.paid,
            'agent_configured': bool(getattr(executor, 'agent_id', None)),
            'input_contract': INPUT_CONTRACT, 'output_contract': OUTPUT_CONTRACT}


# ---- decision -----------------------------------------------------------------------------

def _still_current(store: Store, case: dict[str, Any]) -> list[str]:
    stale = []
    with store.read() as c:
        policy = store.policy(case['policy']['id'], conn=c)
        if policy is None or policy.version != case['policy']['version']:
            stale.append('policy changed during verification')
        for e in case['evidence']:
            doc = store.document(e['document_id'], conn=c)
            if doc is None or doc.version != e['version'] or sha256(doc.content) != e['sha256']:
                stale.append(f'evidence {e["ref"]} ({e["document_id"]}) changed during verification')
    return stale


def decide(case, pre, execution: Execution, verdict, problems, stale) -> tuple[str, str, bool, list[str]]:
    reasons: list[str] = []
    decision, source = 'NEEDS_REVIEW', 'fail_closed'
    if execution.status != 'success':
        reasons.append(execution.error or 'Guard agent did not run')
    elif verdict is None:
        reasons.append('Guard output rejected by server verification: ' + '; '.join(problems))
    else:
        decision, source = verdict, ('test_fixture' if execution.source == 'test_fixture' else 'cominty_guard')
        reasons.append(f'Guard agent verdict {verdict} verified against candidate, policy and evidence bindings.')
    if pre['verdict'] != 'CLEAR' and RANK[pre['verdict']] >= RANK[decision]:
        if RANK[pre['verdict']] > RANK[decision] or source == 'fail_closed':
            decision, source = pre['verdict'], 'deterministic_precheck'
        reasons += [f['reason'] for f in pre['findings'] if f['verdict'] == pre['verdict']]
    if stale:
        if RANK[decision] < RANK['NEEDS_REVIEW']:
            decision, source = 'NEEDS_REVIEW', 'fail_closed'
        reasons += stale
    if case['channel_id'] in w.MANUAL_ONLY_CHANNELS and decision == 'ALLOW':
        decision, source = 'NEEDS_REVIEW', 'channel_boundary'
        reasons.append('Manual-only channel: Guard cannot clear responses here; a human must decide.')
    eligible = decision == 'ALLOW' and source == 'cominty_guard'
    return decision, source, eligible, list(dict.fromkeys(reasons))


def run_check(store: Store, journal: GuardJournal, executor, *, candidate: Any, channel_id: Any,
              evidence_document_ids: Any = (), facts: Any = (), requested_by: str,
              confirm_paid: bool = False) -> dict[str, Any]:
    """Validate one candidate. Never sends, publishes, approves or applies anything."""
    if executor.paid and confirm_paid is not True:
        raise w.Conflict('Real Cominty Guard execution is paid; confirm it explicitly for this request')
    case = build_case(store, candidate=candidate, channel_id=channel_id,
                      evidence_document_ids=evidence_document_ids, facts=facts, requested_by=requested_by)
    pre = precheck(case)
    execution = executor.execute(case)
    verdict, output, problems = (verify_output(case, execution.reply) if execution.status == 'success'
                                 else (None, None, []))
    stale = _still_current(store, case)
    decision, source, eligible, reasons = decide(case, pre, execution, verdict, problems, stale)
    record = {
        'id': case['check_id'], 'database_id': store.identity(), 'created_at': now(),
        'requested_by': requested_by, 'input_contract': INPUT_CONTRACT, 'output_contract': OUTPUT_CONTRACT,
        'channel_id': case['channel_id'], 'candidate_sha256': case['candidate']['sha256'],
        'candidate_length': len(case['candidate']['text']), 'candidate_redacted': redact_pii(case['candidate']['text']),
        'case_sha256': case['case_sha256'], 'policy_id': case['policy']['id'], 'policy_version': case['policy']['version'],
        'evidence': [{k: e[k] for k in ('ref', 'document_id', 'channel_id', 'version', 'sha256')} for e in case['evidence']],
        'facts': [{'ref': f['ref'], 'sha256': sha256(f['statement']), 'statement_redacted': redact_pii(f['statement'])}
                  for f in case['facts']],
        'precheck': pre,
        'execution': {'mode': executor.kind, 'status': execution.status, 'source': execution.source,
                      'error': execution.error, **{k: execution.identity.get(k) for k in (
                          'agent_id', 'thread_id', 'message_id', 'duration_s', 'models', 'tool_calls', 'cost')}},
        'agent_output': config.safe_metadata(output) if output else None,
        'verification': {'ok': verdict is not None and not stale, 'problems': problems, 'stale': stale},
        'decision': decision, 'decision_source': source, 'release_eligible': eligible, 'reasons': reasons,
    }
    journal.record(record)
    store.record_event('guard.check_completed', actor=f'reviewer:{requested_by}', subject=('guard', case['check_id']),
                       status='ok' if execution.status in ('success', 'not_configured') else 'error',
                       metadata={'check_id': case['check_id'], 'candidate_sha256': case['candidate']['sha256'],
                                 'channel_id': case['channel_id'], 'policy_version': case['policy']['version'],
                                 'decision': decision, 'decision_source': source, 'release_eligible': eligible,
                                 'execution_status': execution.status, 'sends_or_publishes': False})
    return journal.get(case['check_id'], store.identity())  # type: ignore[return-value]
