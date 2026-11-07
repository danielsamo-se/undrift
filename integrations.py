"""Cominty platform integrations: version-aware native Memory and Slack notifications.

Memory: Undrift's backend is the only writer, through the native Cominty Memory API
(``client.memory``). It stores only (a) Guard interpretations that a real Cominty Guard
execution produced and the server verified, and (b) facts confirmed by an authenticated
reviewer. Every file is bound to the policy id, version and content fingerprint; files for
any other policy state are historical, never authoritative. Recall is informational: it is
never an input to Guard decisions, clarification or channel autonomy.

Slack: a notification destination only, delivered through Cominty's official Slack
connector by a dedicated Notifier agent (paid, explicitly confirmed per notification). A
delivery counts as confirmed only when Cominty reports a successful Slack send tool call and
the agent's reply echoes the exact notification. Slack never grants approval authority.

All actions are recorded in a separate append-only journal.
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

import clarification
import config
import guard
import workflow as w
from store import Store, now

MEMORY_CONTRACT = 'undrift.memory.v1'
NOTIFY_INPUT_CONTRACT = 'undrift.notify.input.v1'
NOTIFY_OUTPUT_CONTRACT = 'undrift.notify.output.v1'
DEFAULT_NAMESPACE = 'undrift-shiftly-returns'
DEFAULT_SLACK_CONNECTION = 'slack_official'
MAX_FACT = 300
MAX_PURPOSE = 100  # Cominty Memory API limit (HTTP 422 above it)
MAX_NOTIFICATION = 1200
TIMEOUT_S = 120
_SEND_TOOL = re.compile(r'(send|post).*(message)|chat[._]?post|message[._]?(send|post)', re.I)
_SLACK_CHANNEL = re.compile(r'^#?[a-z0-9][a-z0-9_-]{0,79}$|^[CG][A-Z0-9]{8,12}$')
CATEGORY_TEXT = {
    'OUTDATED': 'states a superseded return period as current',
    'EXISTING_COMMITMENT': 'concerns a commitment made before the policy change',
    'VALID_DISTINCT': 'describes a distinct, still valid rule',
    'UNKNOWN': 'needs reviewer judgement',
}

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS memory_writes (
        id TEXT PRIMARY KEY, database_id TEXT NOT NULL, created_at TEXT NOT NULL, actor TEXT NOT NULL,
        namespace TEXT NOT NULL, path TEXT NOT NULL, kind TEXT NOT NULL, policy_id TEXT NOT NULL,
        policy_version INTEGER NOT NULL, policy_sha256 TEXT NOT NULL, content_sha256 TEXT NOT NULL,
        cominty_version TEXT NOT NULL, source_check_id TEXT)""",
    """CREATE TABLE IF NOT EXISTS memory_recalls (
        id TEXT PRIMARY KEY, database_id TEXT NOT NULL, created_at TEXT NOT NULL, check_id TEXT NOT NULL,
        namespace TEXT NOT NULL, status TEXT NOT NULL, matches TEXT NOT NULL, error TEXT)""",
    """CREATE TABLE IF NOT EXISTS notifications (
        id TEXT PRIMARY KEY, database_id TEXT NOT NULL, created_at TEXT NOT NULL, key TEXT NOT NULL,
        kind TEXT NOT NULL, subject_id TEXT NOT NULL, phase TEXT NOT NULL
            CHECK (phase IN ('started','delivered','unconfirmed','failed')),
        actor TEXT NOT NULL, channel TEXT NOT NULL, text_sha256 TEXT NOT NULL, text TEXT NOT NULL,
        execution TEXT NOT NULL, error TEXT)""",
    "CREATE INDEX IF NOT EXISTS notifications_by_key ON notifications(database_id, key, created_at)",
    *(f"""CREATE TRIGGER IF NOT EXISTS {t}_immutable_{op} BEFORE {op.upper()} ON {t}
          BEGIN SELECT RAISE(ABORT, 'integration audit records are immutable'); END"""
      for t in ('memory_writes', 'memory_recalls', 'notifications') for op in ('update', 'delete')),
)
JSON_FIELDS = {'matches', 'execution'}


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


_DATE = re.compile(r'\d{4}-\d{2}-\d{2}(?:[T ][\d:.]+Z?)?')
_EMAIL = re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+')
_PHONE = re.compile(r'\+?\d[\d ()/-]{7,}\d')
_IBAN = re.compile(r'\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}\b')


def redact(text: str) -> str:
    """Secrets, e-mail addresses, IBANs and phone-like numbers; ISO dates are kept (policy facts depend on them)."""
    text = _EMAIL.sub('<email>', config.redact(text))
    text = _IBAN.sub('<iban>', text)
    return _PHONE.sub(lambda m: m.group(0) if _DATE.fullmatch(m.group(0).strip()) else '<number>', text)


def _id(prefix: str) -> str:
    return f'{prefix}-{secrets.token_hex(8)}'


class IntegrationJournal:
    """Append-only audit of memory writes, memory recalls and notification attempts."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        for statement in SCHEMA:
            conn.execute(statement)
        return conn

    def insert(self, table: str, row: dict[str, Any]) -> dict[str, Any]:
        assert table in ('memory_writes', 'memory_recalls', 'notifications')
        conn = self.connect()
        try:
            values = {k: (json.dumps(v, sort_keys=True, default=str) if k in JSON_FIELDS else v) for k, v in row.items()}
            conn.execute(f'INSERT INTO {table} ({", ".join(values)}) VALUES ({", ".join("?" * len(values))})',
                         list(values.values()))
        finally:
            conn.close()
        return row

    def rows(self, table: str, database_id: str, where: str = '', args: tuple = (), limit: int = 50) -> list[dict[str, Any]]:
        assert table in ('memory_writes', 'memory_recalls', 'notifications')
        if not self.path.exists():
            return []
        conn = self.connect()
        try:
            out = []
            for r in conn.execute(f'SELECT * FROM {table} WHERE database_id=? {where} ORDER BY created_at DESC, rowid DESC LIMIT ?',
                                  (database_id, *args, limit)):
                d = dict(r)
                for k in JSON_FIELDS & d.keys():
                    d[k] = json.loads(d[k])
                out.append(d)
            return out
        finally:
            conn.close()


# ---- Cominty Memory -----------------------------------------------------------------------

def policy_fingerprint(policy) -> str:
    return sha256(json.dumps({'id': policy.id, 'version': policy.version, 'days': policy.voluntary_return_days,
                              'effective_from': str(policy.effective_from), 'description': policy.description},
                             sort_keys=True))


class DisabledMemory:
    live = False
    namespace = os.environ.get('UNDRIFT_MEMORY_NAMESPACE', DEFAULT_NAMESPACE)

    def _off(self, *a, **k):
        raise w.Conflict('Cominty Memory is not enabled for this service (UNDRIFT_INTEGRATIONS_LIVE)')

    list = get = create = connectors = _off


def _plain(obj) -> dict[str, Any]:
    return json.loads(obj.model_dump_json()) if hasattr(obj, 'model_dump_json') else dict(vars(obj))


class ComintyMemory:
    """Thin synchronous wrapper over the native Cominty Memory API (free CRUD, no model calls)."""
    live = True

    def __init__(self, namespace: str = DEFAULT_NAMESPACE, client_factory=None) -> None:
        self.namespace, self.client_factory = namespace, client_factory

    def _client(self):
        if self.client_factory:
            return self.client_factory()
        from cominty_sdk import AsyncCominty
        return AsyncCominty()

    def _call(self, fn):
        from cominty_sdk import APIError, ConflictError, NotFoundError

        async def go():
            async with self._client() as client:
                return await fn(client)
        try:
            return asyncio.run(asyncio.wait_for(go(), 30))
        except (NotFoundError, ConflictError):
            raise
        except APIError as exc:
            detail = config.redact(json.dumps(getattr(exc, 'detail', None) or getattr(exc, 'body', None), default=str))[:300]
            raise w.Conflict(f'Cominty Memory rejected the request (HTTP {getattr(exc, "status_code", "?")}): {detail}') from None

    def list(self) -> list[dict[str, Any]]:
        files = self._call(lambda c: c.memory.list(namespace=self.namespace))
        return [_plain(f) for f in files]

    def get(self, path: str) -> dict[str, Any] | None:
        from cominty_sdk import NotFoundError
        try:
            f = self._call(lambda c: c.memory.get(path, namespace=self.namespace))
        except NotFoundError:
            return None
        return _plain(f)

    def create(self, path: str, purpose: str, content: str) -> dict[str, Any]:
        from cominty_sdk import ConflictError
        try:
            f = self._call(lambda c: c.memory.create(path=path, namespace=self.namespace, purpose=purpose, content=content))
        except ConflictError:
            raise w.Conflict(f'a Cominty memory file already exists at {path!r}') from None
        return _plain(f)

    def connectors(self) -> list[dict[str, Any]]:
        servers = self._call(lambda c: c.mcp.list(enabled=None))
        # Never expose MCP URLs or owner ids; status only.
        return [{k: getattr(s, k, None) for k in ('name', 'title', 'is_enabled', 'origin', 'requires_oauth_connect',
                                                   'connection_status')} for s in servers]


def memory_from_env():
    if os.environ.get('UNDRIFT_INTEGRATIONS_LIVE', '0').strip() == '1':
        return ComintyMemory(os.environ.get('UNDRIFT_MEMORY_NAMESPACE', DEFAULT_NAMESPACE).strip() or DEFAULT_NAMESPACE)
    return DisabledMemory()


def render_memory(record: dict[str, Any]) -> str:
    p = record['policy']
    lines = [f"# Undrift {record['kind'].replace('_', ' ')} · {p['id']} v{p['version']}", '',
             f"Authoritative only while policy `{p['id']}` is at version {p['version']} with fingerprint "
             f"`{p['sha256'][:16]}`. For any other policy state this is historical context, not current policy. "
             'Recall never overrides the current policy, Guard checks, clarification or channel autonomy.', '']
    if record['kind'] == 'verified_interpretation':
        lines.append(f"Verified Guard verdict: **{record['verdict']}** (Cominty message `{record['source']['message_id']}`).")
        lines += [f"- “{c['claim']}” → {c['assessment']} [{', '.join(c['evidence_refs'])}]: {c['reason']}" for c in record['claims']]
    else:
        lines.append(f"Reviewer-confirmed fact: {record['statement']}")
    lines += ['', '```json', json.dumps(record, indent=1, sort_keys=True), '```']
    return '\n'.join(lines)


def parse_memory(content: str) -> dict[str, Any] | None:
    blocks = re.findall(r'```json\s*(\{.*?\})\s*```', content or '', re.S)
    try:
        record = json.loads(blocks[-1]) if blocks else None
    except ValueError:
        return None
    if not isinstance(record, dict) or record.get('contract') != MEMORY_CONTRACT:
        return None
    policy = record.get('policy')
    if not isinstance(policy, dict) or not {'id', 'version', 'sha256'} <= policy.keys():
        return None
    return record


def classify(store: Store, record: dict[str, Any] | None) -> str:
    """authoritative | historical | unverified. Only an exact policy match is authoritative."""
    if record is None:
        return 'unverified'
    current = store.policy(record['policy']['id'])
    if current is None:
        return 'unverified'
    same = current.version == record['policy']['version'] and policy_fingerprint(current) == record['policy']['sha256']
    return 'authoritative' if same else 'historical'


def _path(policy, kind: str, digest: str) -> str:
    return f"{policy.id}-v{policy.version}/{kind}-{digest[:16]}.md"


def _write(store: Store, journal: IntegrationJournal, memory, *, policy, kind: str, digest: str, record: dict,
           purpose: str, actor: str, source_check_id: str | None) -> dict[str, Any]:
    if not memory.live:
        raise w.Conflict('Cominty Memory is not enabled for this service (UNDRIFT_INTEGRATIONS_LIVE)')
    path = _path(policy, kind, digest)
    content = render_memory(record)
    created = memory.create(path, purpose[:MAX_PURPOSE], content)
    entry = journal.insert('memory_writes', {
        'id': _id('mem'), 'database_id': store.identity(), 'created_at': now(), 'actor': actor,
        'namespace': memory.namespace, 'path': path, 'kind': kind, 'policy_id': policy.id,
        'policy_version': policy.version, 'policy_sha256': policy_fingerprint(policy),
        'content_sha256': sha256(content), 'cominty_version': str(created.get('version')),
        'source_check_id': source_check_id})
    store.record_event('memory.saved', actor=f'reviewer:{actor}', subject=('memory', path),
                       metadata={'namespace': memory.namespace, 'kind': kind, 'policy_version': policy.version,
                                 'cominty_version': entry['cominty_version'], 'source_check_id': source_check_id})
    return {'write': entry, 'file': {k: created.get(k) for k in ('path', 'namespace', 'version', 'created_at', 'purpose')}}


def save_guard_interpretation(store: Store, guard_journal, journal: IntegrationJournal, memory, *,
                              check_id: str, actor: str) -> dict[str, Any]:
    """Store a Guard verdict in Cominty Memory only if it is a verified real Cominty result for the current policy."""
    check = guard_journal.get(check_id, store.identity()) if guard_journal else None
    if check is None:
        raise w.NotFound(f'unknown guard check {check_id!r}')
    out = check.get('agent_output') or {}
    if check['decision_source'] != 'cominty_guard' or not check['verification']['ok'] or not out:
        raise w.Conflict('only server-verified real Cominty Guard verdicts can be remembered')
    if check['decision'] not in ('ALLOW', 'BLOCK') or check['decision'] != out.get('verdict'):
        raise w.Conflict('unresolved or escalated verdicts are never stored as established interpretations')
    policy = store.policy(check['policy_id'])
    if policy is None or policy.version != check['policy_version']:
        raise w.Conflict('the check was made against an obsolete policy version; it cannot be stored as current')
    record = {
        'contract': MEMORY_CONTRACT, 'kind': 'verified_interpretation',
        'policy': {'id': policy.id, 'version': policy.version, 'sha256': policy_fingerprint(policy)},
        'verdict': check['decision'], 'channel_id': check['channel_id'],
        'candidate_sha256': check['candidate_sha256'], 'candidate_redacted': check['candidate_redacted'],
        'claims': [{'claim': redact(str(f.get('claim'))), 'assessment': f.get('assessment'),
                    'evidence_refs': list(f.get('evidence_refs') or []), 'reason': redact(str(f.get('reason')))}
                   for f in out.get('findings', [])],
        'source': {'guard_check_id': check['id'], 'database_id': check['database_id'],
                   **{k: check['execution'].get(k) for k in ('agent_id', 'thread_id', 'message_id')}},
        'saved_by': actor, 'saved_at': now(),
    }
    return _write(store, journal, memory, policy=policy, kind='guard', digest=check['candidate_sha256'], record=record,
                  purpose=f"Undrift verified Guard verdict {check['decision']} for {policy.id} v{policy.version} only",
                  actor=actor, source_check_id=check['id'])


def save_reviewer_fact(store: Store, journal: IntegrationJournal, memory, *, statement: Any, policy_id: str,
                       actor: str) -> dict[str, Any]:
    if not isinstance(statement, str) or not statement.strip() or len(statement) > MAX_FACT:
        raise w.WorkflowError(f'fact must be 1-{MAX_FACT} characters')
    statement = ' '.join(statement.split())
    if redact(statement) != statement:
        raise w.WorkflowError('fact appears to contain personal data or a secret; it was not stored')
    policy = store.policy(policy_id)
    if policy is None:
        raise w.NotFound(f'unknown policy {policy_id!r}')
    record = {'contract': MEMORY_CONTRACT, 'kind': 'reviewer_fact',
              'policy': {'id': policy.id, 'version': policy.version, 'sha256': policy_fingerprint(policy)},
              'statement': statement, 'source': {'database_id': store.identity()}, 'saved_by': actor, 'saved_at': now()}
    return _write(store, journal, memory, policy=policy, kind='fact', digest=sha256(statement), record=record,
                  purpose=f'Undrift reviewer-confirmed fact for {policy.id} v{policy.version} only',
                  actor=actor, source_check_id=None)


def memory_snapshot(store: Store, journal: IntegrationJournal | None, memory) -> dict[str, Any]:
    out: dict[str, Any] = {'live': memory.live, 'namespace': memory.namespace, 'files': [], 'error': None,
                           'writes': journal.rows('memory_writes', store.identity()) if journal else [],
                           'journal_configured': journal is not None}
    if not memory.live:
        return out
    try:
        summaries = memory.list()
        writes = {wr['path']: wr for wr in out['writes']}
        for s in summaries[:50]:
            full = memory.get(s['path']) or {}
            record = parse_memory(full.get('content', ''))
            ours = writes.get(s['path'])
            out['files'].append({
                'path': s['path'], 'purpose': s.get('purpose'), 'version': s.get('version'),
                'created_at': s.get('created_at'), 'updated_at': s.get('updated_at'), 'status': classify(store, record),
                'record': record, 'written_by_this_service': ours is not None,
                'changed_since_write': bool(ours and ours['content_sha256'] != sha256(full.get('content', ''))),
            })
    except w.WorkflowError:
        raise
    except Exception as exc:  # memory is advisory; failures are visible but never block other work
        out['error'] = f'Cominty Memory unavailable: {type(exc).__name__}'
    return out


def recall_for_check(store: Store, journal: IntegrationJournal | None, memory, check: dict[str, Any]) -> dict[str, Any] | None:
    """Look up remembered interpretations of this exact text. Informational only; never alters the decision."""
    if journal is None or not memory.live:
        return None
    matches, status, error = [], 'ok', None
    try:
        for s in memory.list():
            if not s['path'].endswith(f"/guard-{check['candidate_sha256'][:16]}.md"):
                continue
            record = parse_memory((memory.get(s['path']) or {}).get('content', ''))
            if record and record.get('candidate_sha256') == check['candidate_sha256']:
                matches.append({'path': s['path'], 'version': s.get('version'), 'status': classify(store, record),
                                'verdict': record.get('verdict'), 'policy': record['policy'],
                                'source_check_id': record.get('source', {}).get('guard_check_id'),
                                'message_id': record.get('source', {}).get('message_id'), 'saved_by': record.get('saved_by'),
                                'saved_at': record.get('saved_at')})
    except Exception as exc:
        status, error = 'error', f'Cominty Memory unavailable: {type(exc).__name__}'
    return journal.insert('memory_recalls', {
        'id': _id('rcl'), 'database_id': store.identity(), 'created_at': now(), 'check_id': check['id'],
        'namespace': memory.namespace, 'status': status, 'matches': matches, 'error': error})


def recall_of(store: Store, journal: IntegrationJournal | None, check_id: str) -> dict[str, Any] | None:
    if journal is None:
        return None
    found = journal.rows('memory_recalls', store.identity(), 'AND check_id=?', (check_id,), limit=1)
    return found[0] if found else None


# ---- Slack notifications via Cominty ------------------------------------------------------

def review_link(proposal_id: str) -> str:
    base = os.environ.get('UNDRIFT_REVIEW_BASE_URL', '').strip().rstrip('/') or 'http://127.0.0.1:8503'
    return f'{base}/#/review/{proposal_id}'


def candidates(store: Store) -> list[dict[str, Any]]:
    """Notification candidates built only from verified local records; no agent prose, no customer data."""
    out = []
    docs = {d.id: d for d in store.documents()}
    channels = {c['id']: c['name'] for c in store.channels()}
    for p in store.proposals(status='pending_review'):
        policy = store.policy(p.policy_id, int(p.policy_version))
        history = store.policy_history(p.policy_id)
        previous = next((h for h in history if h.version == policy.version - 1), None) if policy else None
        finding = next(iter(store.findings(document_id=p.document_id) or []), None)
        category = None
        if p.finding_id:
            category = next((f.category for f in store.findings(document_id=p.document_id) if f.id == p.finding_id), None)
        category = category or (finding.category if finding else None)
        doc = docs.get(p.document_id)
        change = (f'{previous.voluntary_return_days} → {policy.voluntary_return_days} days' if previous and policy
                  else f'{policy.voluntary_return_days} days' if policy else 'unknown')
        text = '\n'.join([
            f'Undrift: correction awaiting review ({p.policy_id} v{p.policy_version}).',
            f'Policy change: voluntary returns {change} for purchases from {policy.effective_from if policy else "?"}; '
            'the statutory 14-day withdrawal right is unchanged.',
            f'Affected channel: {channels.get(doc.channel, doc.channel) if doc else "?"} · document {p.document_id}'
            f'{f" ({redact(doc.title)})" if doc else ""}.',
            f'Finding: {category or "drift"}: {CATEGORY_TEXT.get(category or "", "differs from the current policy")}.',
            f'Review (approval happens in Undrift, not in Slack): {review_link(p.id)}'])
        out.append({'key': sha256(f'proposal_pending|{store.identity()}|{p.id}|{p.policy_version}'),
                    'kind': 'proposal_pending', 'subject_id': p.id, 'text': config.redact(text)[:MAX_NOTIFICATION]})
    waiting = [q for c in clarification.listing(store) for ph in c.get('phases', []) if ph.get('state') == 'awaiting_answers'
               for q in ph.get('questions', []) if q.get('answer') is None]
    if waiting:
        subject = ','.join(sorted(str(q.get('id')) for q in waiting))
        out.append({'key': sha256(f'open_questions|{store.identity()}|{subject}'), 'kind': 'open_questions',
                    'subject_id': subject[:200],
                    'text': f'Undrift: {len(waiting)} factual question(s) from the Impact Agent await a reviewer answer '
                            'before any correction can proceed. Answer them in Undrift (Impact map → clarification).'})
    return out


@dataclass
class Delivery:
    status: str  # delivered | unconfirmed | failed
    identity: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class DisabledNotifier:
    kind, paid, live = 'disabled', False, False
    connection = os.environ.get('UNDRIFT_SLACK_CONNECTION', DEFAULT_SLACK_CONNECTION)

    def deliver(self, notification: dict[str, Any], channel: str) -> Delivery:
        raise w.Conflict('Slack notifications are not configured (Notifier agent, live switch and channel required)')


def notifier_message(notification: dict[str, Any], channel: str) -> str:
    payload = {'contract': NOTIFY_INPUT_CONTRACT, 'notification_id': notification['id'], 'channel': channel,
               'text': notification['text'], 'text_sha256': sha256(notification['text'])}
    return ('Post this Undrift notification to Slack exactly once, verbatim, in the given channel only. '
            f'Then reply with the output contract from your instructions.\n```json\n{json.dumps(payload, indent=1)}\n```')


class ComintyNotifier:
    """Paid Cominty execution of the Notifier agent; MCP limited to the single Slack connection."""
    kind, paid, live = 'cominty', True, True

    def __init__(self, agent_id: str, connection: str = DEFAULT_SLACK_CONNECTION, client_factory=None,
                 timeout_s: float = TIMEOUT_S) -> None:
        self.agent_id, self.connection, self.client_factory, self.timeout_s = agent_id, connection, client_factory, timeout_s

    def _client(self):
        if self.client_factory:
            return self.client_factory()
        from cominty_sdk import AsyncCominty
        return AsyncCominty()

    async def _run(self, notification, channel, identity) -> Delivery:
        from cominty_sdk import (AgentCapabilities, ImageGenerationCapability, IndexedDocumentsCapability,
                                 McpCapability, MemoryCapability, MusicGenerationCapability, SkillsCapability,
                                 VideoGenerationCapability, WebCapability, events)
        sends: list[dict[str, Any]] = []
        async with self._client() as client:
            run = await client.chat.start(
                agent_id=self.agent_id, message=notifier_message(notification, channel), name='undrift-notify', max_steps=4,
                thread_capabilities=AgentCapabilities(
                    mcp=McpCapability(activation='on_request', connections=[self.connection]),
                    web=WebCapability(activation='never'), indexed_documents=IndexedDocumentsCapability(activation='never'),
                    skills=SkillsCapability(activation='never'), image_generation=ImageGenerationCapability(activation='never'),
                    video_generation=VideoGenerationCapability(activation='never'),
                    music_generation=MusicGenerationCapability(activation='never'), memory=MemoryCapability(activation='never')))
            identity.update(thread_id=str(run.thread.id), message_id=str(run.message_id))
            async with run:
                async for event in run:
                    if isinstance(event, events.ToolCall) and event.status != 'running':
                        call = {'name': str(event.data.name)[:80], 'status': event.status, 'error': (event.data.error or '')[:200] or None}
                        identity['tool_calls'].append(call)
                        if _SEND_TOOL.search(call['name']):
                            sends.append(call)
                    elif isinstance(event, events.Result):
                        identity['cost'] = json.loads(event.data.cost.model_dump_json())
            message = await run.result()
        status = getattr(message.status, 'value', message.status)
        reply = _parse_reply(message.content or '')
        ok_sends = [s for s in sends if s['status'] == 'success']
        identity['slack'] = {k: reply.get(k) for k in ('message_ts', 'permalink', 'channel')} if reply else None
        if (status == 'success' and len(ok_sends) == 1 and reply and reply.get('posted') is True
                and reply.get('notification_id') == notification['id'] and reply.get('text_sha256') == sha256(notification['text'])
                and reply.get('channel') == channel and reply.get('message_ts')):
            return Delivery('delivered', identity)
        if not sends and status != 'success':
            return Delivery('failed', identity, f'Cominty execution status {status}; no Slack send was attempted')
        return Delivery('unconfirmed', identity, 'Slack delivery could not be confirmed from Cominty evidence; inspect '
                        'the channel before any retry' + (' (multiple send calls)' if len(ok_sends) > 1 else ''))

    def deliver(self, notification: dict[str, Any], channel: str) -> Delivery:
        identity: dict[str, Any] = {'agent_id': self.agent_id, 'connection': self.connection, 'tool_calls': [], 'cost': None}
        started = time.monotonic()
        try:
            result = asyncio.run(asyncio.wait_for(self._run(notification, channel, identity), self.timeout_s))
        except (TimeoutError, asyncio.TimeoutError):
            result = Delivery('unconfirmed', identity, f'no confirmation within {self.timeout_s:.0f} s; inspect before retrying')
        except Exception as exc:
            started_remote = bool(identity.get('message_id'))
            result = Delivery('unconfirmed' if started_remote else 'failed', identity,
                              f'Cominty notification execution failed: {type(exc).__name__}')
        identity['duration_s'] = round(time.monotonic() - started, 2)
        return result


def _parse_reply(content: str) -> dict[str, Any] | None:
    blocks = re.findall(r'```json\s*(\{.*?\})\s*```', content, re.S)
    try:
        out = json.loads(blocks[-1]) if blocks else None
    except ValueError:
        return None
    return out if isinstance(out, dict) and out.get('contract') == NOTIFY_OUTPUT_CONTRACT else None


def slack_channel() -> str | None:
    channel = os.environ.get('UNDRIFT_SLACK_CHANNEL', '').strip()
    return channel if channel and _SLACK_CHANNEL.fullmatch(channel) else None


def notifier_from_env():
    agent_id = os.environ.get('UNDRIFT_NOTIFY_AGENT_ID', '').strip()
    if os.environ.get('UNDRIFT_NOTIFY_LIVE', '0').strip() == '1' and agent_id and slack_channel():
        return ComintyNotifier(agent_id, os.environ.get('UNDRIFT_SLACK_CONNECTION', DEFAULT_SLACK_CONNECTION).strip())
    return DisabledNotifier()


def latest_attempt(store: Store, journal: IntegrationJournal, key: str) -> dict[str, Any] | None:
    found = journal.rows('notifications', store.identity(), 'AND key=?', (key,), limit=1)
    return found[0] if found else None


def send_notification(store: Store, journal: IntegrationJournal | None, notifier, *, key: str, actor: str,
                      confirm_paid: bool = False) -> dict[str, Any]:
    """Deliver one notification at most once. Never approves, applies or publishes anything."""
    if journal is None:
        raise w.Conflict('integration audit journal is not configured for this service')
    channel = slack_channel()
    if not notifier.live or channel is None:
        raise w.Conflict('Slack notifications are not configured (Notifier agent, live switch and authorized channel required)')
    if notifier.paid and confirm_paid is not True:
        raise w.Conflict('Slack delivery runs a paid Cominty execution; confirm it explicitly for this notification')
    item = next((c for c in candidates(store) if c['key'] == key), None)
    if item is None:
        raise w.NotFound('unknown or no longer current notification')
    last = latest_attempt(store, journal, key)
    if last and last['phase'] != 'failed':
        raise w.Conflict(f"notification already {last['phase']}; duplicates are not sent"
                         + ('' if last['phase'] == 'delivered' else ' (inspect Slack before any manual retry)'))
    note_id = _id('ntf')
    base = {'database_id': store.identity(), 'key': key, 'kind': item['kind'], 'subject_id': item['subject_id'],
            'actor': actor, 'channel': channel, 'text_sha256': sha256(item['text']), 'text': item['text']}
    journal.insert('notifications', {**base, 'id': note_id, 'created_at': now(), 'phase': 'started',
                                     'execution': {'mode': notifier.kind}, 'error': None})
    result = notifier.deliver({'id': note_id, 'text': item['text']}, channel)
    row = journal.insert('notifications', {**base, 'id': _id('ntf'), 'created_at': now(), 'phase': result.status,
                                           'execution': {'mode': notifier.kind, 'attempt_id': note_id, **result.identity},
                                           'error': result.error})
    store.record_event('notification.' + result.status, actor=f'reviewer:{actor}', subject=('notification', note_id),
                       status='ok' if result.status == 'delivered' else 'error',
                       metadata={'kind': item['kind'], 'subject_id': item['subject_id'], 'destination': 'slack',
                                 'grants_approval': False, 'phase': result.status})
    return row


def notifications_snapshot(store: Store, journal: IntegrationJournal | None, notifier, memory) -> dict[str, Any]:
    connector, connector_error = None, None
    if memory.live:
        try:
            servers = memory.connectors()
            connector = next((s for s in servers if s['name'] == notifier.connection), None)
            connector = connector or {'name': notifier.connection, 'missing': True}
            connector['alternatives'] = [s['name'] for s in servers if 'slack' in (s['name'] or '') and s['name'] != notifier.connection]
        except Exception as exc:
            connector_error = f'Cominty connector status unavailable: {type(exc).__name__}'
    deliveries = journal.rows('notifications', store.identity()) if journal else []
    latest: dict[str, dict] = {}
    for d in reversed(deliveries):
        latest[d['key']] = d
    items = [{**c, 'last_phase': latest.get(c['key'], {}).get('phase')} for c in candidates(store)]
    return {'mode': notifier.kind, 'paid': notifier.paid, 'connection': notifier.connection,
            'agent_configured': bool(getattr(notifier, 'agent_id', None) or os.environ.get('UNDRIFT_NOTIFY_AGENT_ID', '').strip()),
            'channel': slack_channel(), 'connector': connector, 'connector_error': connector_error,
            'journal_configured': journal is not None, 'candidates': items, 'deliveries': deliveries,
            'grants_approval': False}
