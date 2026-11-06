"""SQLite persistence: policies, channels, documents, proposals and the activity log.

One short-lived connection per operation, WAL mode, and BEGIN IMMEDIATE for every write,
so the MCP server (tool handlers run in worker threads), the review API and the CLI can
share one database file. Event timestamps are taken while holding the write lock, so
`seq` order and `ts` order agree.
"""

from __future__ import annotations

import json
import hashlib
import re
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import config
import fixtures
from models import AgentRun, Document, Event, Finding, Policy, Proposal

SCHEMA_VERSION = 3
EVENT_TYPE_RE = re.compile(r"^[a-z_]+(\.[a-z_]+)+$")
SYSTEM = "system"

SCHEMA = """
CREATE TABLE IF NOT EXISTS store_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS policies (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    policy_type TEXT NOT NULL,
    current_version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS policy_versions (
    policy_id TEXT NOT NULL REFERENCES policies(id),
    version INTEGER NOT NULL,
    voluntary_return_days INTEGER NOT NULL,
    effective_from TEXT NOT NULL,
    description TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    PRIMARY KEY (policy_id, version)
);
CREATE TABLE IF NOT EXISTS channels (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    audience TEXT NOT NULL CHECK (audience IN ('public', 'internal'))
);
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    channel_id TEXT NOT NULL REFERENCES channels(id),
    title TEXT NOT NULL,
    current_version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS document_versions (
    document_id TEXT NOT NULL REFERENCES documents(id),
    version INTEGER NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    PRIMARY KEY (document_id, version)
);
CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id),
    base_version INTEGER NOT NULL,
    policy_id TEXT NOT NULL,
    policy_version INTEGER NOT NULL,
    original_content TEXT NOT NULL,
    proposed_content TEXT NOT NULL,
    diff TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending_review', 'approved', 'rejected', 'applied')),
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    provenance TEXT NOT NULL DEFAULT '{}',
    run_id TEXT REFERENCES agent_runs(id),
    finding_id TEXT REFERENCES findings(id),
    evidence TEXT NOT NULL DEFAULT '',
    reviewed_by TEXT,
    reviewed_at TEXT,
    review_note TEXT,
    applied_version INTEGER,
    applied_at TEXT,
    FOREIGN KEY (document_id, base_version) REFERENCES document_versions(document_id, version),
    FOREIGN KEY (policy_id, policy_version) REFERENCES policy_versions(policy_id, version)
);
CREATE INDEX IF NOT EXISTS proposals_by_document ON proposals(document_id, status);
CREATE TABLE IF NOT EXISTS agent_runs (
    id TEXT PRIMARY KEY,
    policy_id TEXT NOT NULL,
    policy_version INTEGER NOT NULL,
    document_ids TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN
        ('open', 'completed', 'incomplete', 'failed', 'expired', 'interrupted')),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    closed_at TEXT,
    agent_id TEXT,
    thread_id TEXT,
    message_id TEXT,
    document_versions TEXT NOT NULL DEFAULT '{}',
    lease_until TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    FOREIGN KEY (policy_id, policy_version) REFERENCES policy_versions(policy_id, version)
);
CREATE TABLE IF NOT EXISTS findings (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES agent_runs(id),
    policy_id TEXT NOT NULL,
    policy_version INTEGER NOT NULL,
    document_id TEXT NOT NULL,
    document_version INTEGER,
    category TEXT NOT NULL CHECK (category IN
        ('OUTDATED', 'VALID_DISTINCT', 'EXISTING_COMMITMENT', 'UNRELATED', 'UNKNOWN')),
    evidence TEXT NOT NULL,
    reason TEXT NOT NULL,
    retrieved_during_run INTEGER NOT NULL,
    submission_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'mcp' CHECK (source IN ('mcp', 'final_json', 'legacy')),
    UNIQUE (run_id, document_id),
    FOREIGN KEY (document_id, document_version) REFERENCES document_versions(document_id, version),
    FOREIGN KEY (policy_id, policy_version) REFERENCES policy_versions(policy_id, version)
);
CREATE INDEX IF NOT EXISTS findings_by_document ON findings(document_id, created_at);
CREATE TABLE IF NOT EXISTS run_retrievals (
    run_id TEXT NOT NULL REFERENCES agent_runs(id),
    kind TEXT NOT NULL CHECK (kind IN ('policy', 'document')),
    subject_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ok', 'error')),
    error TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, kind, subject_id, version)
);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    type TEXT NOT NULL,
    actor TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('ok', 'error', 'denied')),
    subject_type TEXT,
    subject_id TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    error TEXT
);
CREATE INDEX IF NOT EXISTS events_by_subject ON events(subject_type, subject_id);
"""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


class Store:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else config.db_path()
        self._snapshot = ContextVar("store_snapshot", default=None)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def write(self, existing: sqlite3.Connection | None = None) -> Iterator[sqlite3.Connection]:
        if existing is not None:
            yield existing
            return
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    @contextmanager
    def snapshot(self):
        """Reuse one read transaction throughout a projection, isolated per context."""
        with self.read() as conn:
            token = self._snapshot.set(conn)
            try:
                yield conn
            finally:
                self._snapshot.reset(token)

    @contextmanager
    def read(self, conn: sqlite3.Connection | None = None) -> Iterator[sqlite3.Connection]:
        conn = conn if conn is not None else self._snapshot.get()
        if conn is not None:
            yield conn
            return
        conn = self.connect()
        try:
            conn.execute("BEGIN")
            yield conn
        finally:
            if conn.in_transaction:
                conn.execute("COMMIT")
            conn.close()

    def init(self, seed: bool = True) -> bool:
        """Transactional, additive v1/v2 migration. Never resets policies or proposals."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = self.connect()
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError("database schema is newer than this application")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("BEGIN IMMEDIATE")
            # Re-read under the lock: two services may start at the same time.
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError("database schema is newer than this application")
            if version < SCHEMA_VERSION:
                if version == 2:
                    conn.execute("ALTER TABLE findings RENAME TO findings_v2")
                    conn.execute("ALTER TABLE agent_runs RENAME TO agent_runs_v2")
                    conn.execute("DROP INDEX IF EXISTS findings_by_document")
                for statement in SCHEMA.split(";"):
                    if statement.strip():
                        conn.execute(statement)
                columns = {r[1] for r in conn.execute("PRAGMA table_info(proposals)")}
                for name, definition in (
                    ("run_id", "TEXT REFERENCES agent_runs(id)"),
                    ("finding_id", "TEXT REFERENCES findings(id)"),
                    ("evidence", "TEXT NOT NULL DEFAULT ''"),
                ):
                    if name not in columns:
                        conn.execute(f"ALTER TABLE proposals ADD COLUMN {name} {definition}")
                if version == 2:
                    conn.execute(
                        "INSERT INTO agent_runs SELECT id, policy_id, policy_version, document_ids, "
                        "token_hash, CASE WHEN status = 'open' THEN 'interrupted' ELSE status END, "
                        "created_at, expires_at, COALESCE(closed_at, ?), agent_id, thread_id, "
                        "message_id, '{}', expires_at, '{}', NULL FROM agent_runs_v2", (now(),))
                    conn.execute("INSERT INTO findings SELECT id, run_id, policy_id, policy_version, "
                                 "document_id, document_version, category, evidence, reason, 0, "
                                 "submission_sha256, created_at, 'legacy' FROM findings_v2")
                    conn.execute("DROP TABLE findings_v2")
                    conn.execute("DROP TABLE agent_runs_v2")
                self._import_legacy_runs(conn)
                conn.execute("INSERT OR IGNORE INTO store_metadata VALUES ('database_id', ?)",
                             (uuid.uuid4().hex,))
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
        return self.seed() if seed else False

    def identity(self) -> str:
        with self.read() as conn:
            return conn.execute("SELECT value FROM store_metadata WHERE key = 'database_id'").fetchone()[0]

    def _import_legacy_runs(self, conn: sqlite3.Connection) -> None:
        """Freeze historical event-based runs once; old evidence files remain optional."""
        events = [dict(r) for r in conn.execute("SELECT * FROM events ORDER BY seq")]
        for e in events:
            e['metadata'] = json.loads(e['metadata'])
        starts = [e for e in events if e['type'] == 'agent.run_started'
                  and not e['metadata'].get('run_id')]
        for i, start in enumerate(starts):
            stop = starts[i + 1]['seq'] if i + 1 < len(starts) else float('inf')
            finish = next((e for e in events if e['type'] == 'agent.run_finished'
                           and start['seq'] < e['seq'] < stop), None)
            meta = finish['metadata'] if finish else {}
            mid = meta.get('message_id')
            rid = 'run-' + hashlib.sha256(f"legacy:{start['seq']}:{mid}".encode()).hexdigest()[:16]
            results = [e for e in events if e['type'] == 'agent.classification'
                       and e['status'] == 'ok' and mid
                       and (e['metadata'].get('provenance') or {}).get('message_id') == mid]
            policy_row = conn.execute("SELECT id, current_version FROM policies ORDER BY id LIMIT 1").fetchone()
            if policy_row is None:
                continue
            pid, pv = policy_row
            historical = conn.execute("SELECT version FROM policy_versions WHERE policy_id = ? "
                                      "AND created_at <= ? ORDER BY version DESC LIMIT 1",
                                      (pid, start['ts'])).fetchone()
            if historical:
                pv = historical['version']
            ids = [e['subject_id'] for e in results]
            versions = {e['subject_id']: e['metadata']['document_version'] for e in results
                        if type(e['metadata'].get('document_version')) is int}
            error = next((e['error'] for e in events if e['status'] == 'error'
                          and mid and (e['metadata'].get('provenance') or {}).get('message_id') == mid), None)
            execution = meta.get('execution_status', meta.get('status'))
            if finish and (finish['status'] != 'ok' or execution in {'failed', 'cancelled', 'error'}):
                status = 'interrupted' if execution == 'cancelled' else 'failed'
                error = finish['error'] or error or 'Historical execution did not succeed'
            else:
                status = 'interrupted' if not finish else 'incomplete' if error else 'completed'
            conn.execute(
                "INSERT OR IGNORE INTO agent_runs (id, policy_id, policy_version, document_ids, "
                "token_hash, status, created_at, expires_at, closed_at, agent_id, thread_id, "
                "message_id, document_versions, lease_until, metadata, error) "
                "VALUES (?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (rid, pid, pv, json.dumps(ids), status, start['ts'], start['ts'],
                 finish['ts'] if finish else now(), start['metadata'].get('agent_id'),
                 meta.get('thread_id'), mid, json.dumps(versions), start['ts'],
                 json.dumps(config.safe_metadata({**meta, 'legacy': True,
                     'legacy_start_seq': start['seq'],
                     'legacy_finish_seq': finish['seq'] if finish else None})), error))
            for e in results:
                m = e['metadata']
                if m.get('category') not in {'OUTDATED', 'VALID_DISTINCT', 'EXISTING_COMMITMENT', 'UNRELATED', 'UNKNOWN'}:
                    continue
                fid = f"fnd-legacy-{e['seq']}"
                conn.execute(
                    "INSERT OR IGNORE INTO findings VALUES (?, ?, ?, ?, ?, ?, ?, '', '', 0, '', ?, 'legacy')",
                    (fid, rid, pid, m.get('policy_version', pv), e['subject_id'],
                     versions.get(e['subject_id']), m['category'], e['ts']))
            if mid:
                for p in conn.execute("SELECT id, document_id, provenance FROM proposals").fetchall():
                    if json.loads(p['provenance']).get('message_id') == mid:
                        f = conn.execute("SELECT id FROM findings WHERE run_id = ? AND document_id = ?",
                                         (rid, p['document_id'])).fetchone()
                        conn.execute("UPDATE proposals SET run_id = ?, finding_id = ? WHERE id = ?",
                                     (rid, f['id'] if f else None, p['id']))

    def seed(self) -> bool:
        with self.write() as conn:
            if conn.execute("SELECT COUNT(*) FROM policies").fetchone()[0]:
                return False
            p = fixtures.SEED_POLICY
            conn.execute("INSERT INTO policies VALUES (?, ?, ?, ?)",
                         (p.id, fixtures.POLICY_TITLE, p.policy_type, p.version))
            conn.execute("INSERT INTO policy_versions VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (p.id, p.version, p.voluntary_return_days, p.effective_from.isoformat(),
                          p.description, now(), SYSTEM))
            conn.executemany("INSERT INTO channels VALUES (?, ?, ?)",
                             [(cid, name, aud) for cid, (name, aud) in fixtures.CHANNELS.items()])
            for d in fixtures.DOCUMENTS.values():
                conn.execute("INSERT INTO documents VALUES (?, ?, ?, ?)",
                             (d.id, d.channel, d.title, d.version))
                conn.execute("INSERT INTO document_versions VALUES (?, ?, ?, ?, ?)",
                             (d.id, d.version, d.content, d.updated_at.isoformat(), SYSTEM))
            self.add_event(conn, "store.seeded", actor=SYSTEM, metadata={
                "policies": [p.id], "documents": sorted(fixtures.DOCUMENTS)})
            return True

    def policy(self, policy_id: str, version: int | None = None,
               conn: sqlite3.Connection | None = None) -> Policy | None:
        with self.read(conn) as c:
            row = c.execute(
                "SELECT p.id, p.policy_type, v.* FROM policies p JOIN policy_versions v "
                "ON v.policy_id = p.id AND v.version = COALESCE(?, p.current_version) "
                "WHERE p.id = ?", (version, policy_id)).fetchone()
        return None if row is None else Policy(
            id=row["id"], version=row["version"], policy_type=row["policy_type"],
            voluntary_return_days=row["voluntary_return_days"],
            effective_from=row["effective_from"], description=row["description"])

    def policy_ids(self) -> list[str]:
        with self.read() as c:
            return [r[0] for r in c.execute("SELECT id FROM policies ORDER BY id")]

    def policy_history(self, policy_id: str) -> list[Policy]:
        with self.read() as c:
            versions = [r[0] for r in c.execute(
                "SELECT version FROM policy_versions WHERE policy_id = ? ORDER BY version",
                (policy_id,))]
            return [p for v in versions if (p := self.policy(policy_id, v, c))]

    _DOC_SQL = ("SELECT d.id, d.channel_id, d.title, v.version, v.content, v.created_at "
                "FROM documents d JOIN document_versions v ON v.document_id = d.id ")

    @staticmethod
    def _document(row: sqlite3.Row) -> Document:
        return Document(id=row["id"], channel=row["channel_id"], title=row["title"],
                        content=row["content"], version=row["version"],
                        updated_at=row["created_at"])

    def document(self, document_id: str, version: int | None = None,
                 conn: sqlite3.Connection | None = None) -> Document | None:
        with self.read(conn) as c:
            row = c.execute(self._DOC_SQL + "AND v.version = COALESCE(?, d.current_version) "
                            "WHERE d.id = ?", (version, document_id)).fetchone()
        return None if row is None else self._document(row)

    def documents(self, conn: sqlite3.Connection | None = None) -> list[Document]:
        with self.read(conn) as c:
            rows = c.execute(self._DOC_SQL + "AND v.version = d.current_version ORDER BY d.id")
            return [self._document(r) for r in rows]

    def document_ids(self) -> list[str]:
        with self.read() as c:
            return [r[0] for r in c.execute("SELECT id FROM documents ORDER BY id")]

    def channels(self) -> list[dict[str, str]]:
        with self.read() as c:
            return [dict(r) for r in c.execute("SELECT * FROM channels ORDER BY id")]

    @staticmethod
    def add_event(conn: sqlite3.Connection, type: str, *, actor: str, status: str = "ok",
                  subject: tuple[str, str] | None = None, metadata: dict[str, Any] | None = None,
                  error: str | None = None) -> int:
        """Append an event inside the caller's write transaction."""
        if not EVENT_TYPE_RE.match(type):
            raise ValueError(f"invalid event type {type!r}")
        subject_type, subject_id = subject or (None, None)
        subject_id = config.safe_text(subject_id, 128) if subject_id else None
        cur = conn.execute(
            "INSERT INTO events (ts, type, actor, status, subject_type, subject_id, metadata, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (now(), type, config.safe_text(actor, 120), status, subject_type, subject_id,
             json.dumps(config.safe_metadata(metadata or {}), sort_keys=True, default=str),
             config.safe_text(error, 500) if error else None))
        return int(cur.lastrowid)

    def record_event(self, type: str, **kwargs: Any) -> int:
        with self.write() as conn:
            return self.add_event(conn, type, **kwargs)

    def events(self, *, after: int = 0, limit: int = 100, type_prefix: str | None = None,
               subject: tuple[str, str] | None = None, latest: bool = False) -> list[Event]:
        """Events in ascending seq order; with latest=True, the newest `limit` matching ones."""
        sql, args = "SELECT * FROM events WHERE seq > ?", [after]
        if type_prefix:
            sql += " AND type LIKE ? ESCAPE '\\'"
            args.append(type_prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
        if subject:
            sql += " AND subject_type = ? AND subject_id = ?"
            args += list(subject)
        sql += f" ORDER BY seq {'DESC' if latest else 'ASC'} LIMIT ?"
        args.append(max(1, min(limit, 100_000)))
        with self.read() as c:
            rows = [Event(**{**dict(r), "metadata": json.loads(r["metadata"])})
                    for r in c.execute(sql, args)]
        return rows[::-1] if latest else rows

    @staticmethod
    def _proposal(row: sqlite3.Row) -> Proposal:
        return Proposal(**{**dict(row), "provenance": json.loads(row["provenance"])})

    def proposal(self, proposal_id: str, conn: sqlite3.Connection | None = None) -> Proposal | None:
        with self.read(conn) as c:
            row = c.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
        return None if row is None else self._proposal(row)

    def proposals(self, status: str | None = None, document_id: str | None = None,
                  conn: sqlite3.Connection | None = None) -> list[Proposal]:
        sql, args = "SELECT * FROM proposals WHERE 1 = 1", []
        if status:
            sql += " AND status = ?"
            args.append(status)
        if document_id:
            sql += " AND document_id = ?"
            args.append(document_id)
        with self.read(conn) as c:
            return [self._proposal(r) for r in c.execute(sql + " ORDER BY created_at", args)]

    @staticmethod
    def _run(row: sqlite3.Row) -> AgentRun:
        data = {k: row[k] for k in row.keys() if k != "token_hash"}
        return AgentRun(**{**data, "document_ids": json.loads(row["document_ids"]),
                           "document_versions": json.loads(row["document_versions"]),
                           "metadata": json.loads(row["metadata"])})

    def run(self, run_id: str, conn: sqlite3.Connection | None = None) -> AgentRun | None:
        with self.read(conn) as c:
            row = c.execute("SELECT * FROM agent_runs WHERE id = ?", (run_id,)).fetchone()
        return None if row is None else self._run(row)

    def runs(self, conn: sqlite3.Connection | None = None) -> list[AgentRun]:
        with self.read(conn) as c:
            return [self._run(r) for r in c.execute("SELECT * FROM agent_runs ORDER BY created_at")]

    @staticmethod
    def _finding(row: sqlite3.Row) -> Finding:
        data = {k: row[k] for k in row.keys() if k != "submission_sha256"}
        return Finding(**{**data, "retrieved_during_run": bool(row["retrieved_during_run"])})

    def findings(self, run_id: str | None = None, document_id: str | None = None,
                 conn: sqlite3.Connection | None = None) -> list[Finding]:
        sql, args = "SELECT * FROM findings WHERE 1 = 1", []
        if run_id:
            sql += " AND run_id = ?"
            args.append(run_id)
        if document_id:
            sql += " AND document_id = ?"
            args.append(document_id)
        with self.read(conn) as c:
            return [self._finding(r) for r in c.execute(sql + " ORDER BY created_at, id", args)]

    def reap_runs(self) -> int:
        """Expire capabilities and interrupted runner leases, once, with audit events."""
        with self.read() as conn:
            due = conn.execute("SELECT 1 FROM agent_runs WHERE status = 'open' AND "
                               "(expires_at <= ? OR lease_until <= ?) LIMIT 1", (now(), now())).fetchone()
        if not due:
            return 0
        with self.write() as conn:
            rows = conn.execute("SELECT * FROM agent_runs WHERE status = 'open' AND "
                                "(expires_at <= ? OR lease_until <= ?)", (now(), now())).fetchall()
            for row in rows:
                status = 'expired' if row['expires_at'] <= now() else 'interrupted'
                error = 'Run lifetime expired' if status == 'expired' else 'Runner heartbeat stopped'
                conn.execute("UPDATE agent_runs SET status = ?, closed_at = ?, error = ? WHERE id = ?",
                             (status, now(), error, row['id']))
                self.add_event(conn, 'agent.run_finished', actor='system', status='error',
                               subject=('run', row['id']), metadata={'run_id': row['id'],
                               'run_status': status}, error=error)
            return len(rows)
