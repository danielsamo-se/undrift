"""Additive local conversation journal. Never imports historical evidence."""

STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS conversations (
        id TEXT PRIMARY KEY REFERENCES agent_runs(id),
        agent_id TEXT NOT NULL, connection TEXT NOT NULL, instructions_sha256 TEXT NOT NULL,
        thread_id TEXT UNIQUE, state TEXT NOT NULL,
        max_turns INTEGER NOT NULL, max_total_steps INTEGER NOT NULL,
        max_reported_cost TEXT NOT NULL, created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS conversation_phases (
        run_id TEXT PRIMARY KEY REFERENCES agent_runs(id),
        conversation_id TEXT NOT NULL REFERENCES conversations(id),
        ordinal INTEGER NOT NULL, max_steps INTEGER NOT NULL,
        message_id TEXT UNIQUE, state TEXT NOT NULL, cost TEXT,
        created_at TEXT NOT NULL, finished_at TEXT,
        UNIQUE(conversation_id, ordinal))""",
    """CREATE TABLE IF NOT EXISTS clarification_questions (
        id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES conversation_phases(run_id),
        ordinal INTEGER NOT NULL, document_id TEXT NOT NULL,
        document_version INTEGER NOT NULL, policy_version INTEGER NOT NULL,
        prompt TEXT NOT NULL, options TEXT NOT NULL, evidence TEXT NOT NULL,
        answer TEXT, answered_by TEXT, answered_at TEXT,
        UNIQUE(run_id, ordinal),
        FOREIGN KEY(document_id, document_version) REFERENCES document_versions(document_id, version))""",
    """CREATE TABLE IF NOT EXISTS conversation_evidence (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT NOT NULL REFERENCES conversation_phases(run_id),
        kind TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL)""",
]
