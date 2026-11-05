"""SQLite persistence: policies, channels, documents, proposals and the activity log.

One short-lived connection per operation, WAL mode, and BEGIN IMMEDIATE for every write,
so the MCP server (tool handlers run in worker threads), the review API and the CLI can
share one database file. Event timestamps are taken while holding the write lock, so
`seq` order and `ts` order agree.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import config
import fixtures
from models import Document, Event, Policy, Proposal

SCHEMA_VERSION = 1
EVENT_TYPE_RE = re.compile(r"^[a-z_]+(\.[a-z_]+)+$")
SYSTEM = "system"

SCHEMA = """
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
    reviewed_by TEXT,
    reviewed_at TEXT,
    review_note TEXT,
    applied_version INTEGER,
    applied_at TEXT,
    FOREIGN KEY (document_id, base_version) REFERENCES document_versions(document_id, version),
    FOREIGN KEY (policy_id, policy_version) REFERENCES policy_versions(policy_id, version)
);
CREATE INDEX IF NOT EXISTS proposals_by_document ON proposals(document_id, status);
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

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
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
    def read(self, conn: sqlite3.Connection | None = None) -> Iterator[sqlite3.Connection]:
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
        """Create the schema (idempotent); seed the Shiftly data into an empty database."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = self.connect()
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(SCHEMA)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        finally:
            conn.close()
        return self.seed() if seed else False

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
