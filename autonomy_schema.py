"""Additive schema-v6 channel delegation, run-pinned grants and immutable receipts."""
STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS channel_autonomy (
        channel_id TEXT PRIMARY KEY REFERENCES channels(id),
        version INTEGER NOT NULL CHECK(version>0),
        mode TEXT NOT NULL CHECK(mode IN ('review_required','bounded_auto','pull_request','manual_only')),
        max_per_run INTEGER NOT NULL CHECK(max_per_run BETWEEN 1 AND 3),
        updated_at TEXT NOT NULL, updated_by TEXT NOT NULL, note TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS run_autonomy_grants (
        run_id TEXT NOT NULL REFERENCES agent_runs(id),
        document_id TEXT NOT NULL REFERENCES documents(id),
        channel_id TEXT NOT NULL, policy_version INTEGER NOT NULL,
        mode TEXT NOT NULL, max_per_run INTEGER NOT NULL,
        PRIMARY KEY(run_id,document_id))""",
    """CREATE TABLE IF NOT EXISTS autonomy_applications (
        proposal_id TEXT PRIMARY KEY REFERENCES proposals(id),
        run_id TEXT NOT NULL REFERENCES agent_runs(id), document_id TEXT NOT NULL,
        channel_id TEXT NOT NULL, policy_version INTEGER NOT NULL,
        original_sha256 TEXT NOT NULL, proposed_sha256 TEXT NOT NULL,
        applied_version INTEGER NOT NULL, applied_at TEXT NOT NULL,
        UNIQUE(run_id,document_id))""",
    """CREATE TRIGGER IF NOT EXISTS immutable_autonomy_grant_update BEFORE UPDATE ON run_autonomy_grants
        BEGIN SELECT RAISE(ABORT,'run autonomy grants are immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS immutable_autonomy_grant_delete BEFORE DELETE ON run_autonomy_grants
        BEGIN SELECT RAISE(ABORT,'run autonomy grants are immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS immutable_autonomy_receipt_update BEFORE UPDATE ON autonomy_applications
        BEGIN SELECT RAISE(ABORT,'autonomy receipts are immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS immutable_autonomy_receipt_delete BEFORE DELETE ON autonomy_applications
        BEGIN SELECT RAISE(ABORT,'autonomy receipts are immutable'); END""",
)
