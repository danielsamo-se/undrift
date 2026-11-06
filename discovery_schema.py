"""Additive schema-v4 discovery index and immutable optional finding corrections."""

STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS finding_corrections (finding_id TEXT PRIMARY KEY REFERENCES findings(id), proposed_content TEXT NOT NULL)",
    "CREATE VIRTUAL TABLE IF NOT EXISTS document_search USING fts5(document_id UNINDEXED, channel_id UNINDEXED, version UNINDEXED, title, content, tokenize='unicode61')",
    """CREATE TRIGGER IF NOT EXISTS search_document_update AFTER UPDATE ON documents BEGIN
        DELETE FROM document_search WHERE document_id = OLD.id;
        INSERT INTO document_search SELECT NEW.id, NEW.channel_id, v.version, NEW.title, v.content
        FROM document_versions v WHERE v.document_id = NEW.id AND v.version = NEW.current_version;
    END""",
    """CREATE TRIGGER IF NOT EXISTS search_document_delete AFTER DELETE ON documents BEGIN
        DELETE FROM document_search WHERE document_id = OLD.id;
    END""",
    """CREATE TRIGGER IF NOT EXISTS search_version_insert AFTER INSERT ON document_versions
    WHEN NEW.version = (SELECT current_version FROM documents WHERE id = NEW.document_id) BEGIN
        DELETE FROM document_search WHERE document_id = NEW.document_id;
        INSERT INTO document_search SELECT d.id, d.channel_id, NEW.version, d.title, NEW.content
        FROM documents d WHERE d.id = NEW.document_id;
    END""",
    """CREATE TRIGGER IF NOT EXISTS search_version_update AFTER UPDATE ON document_versions
    WHEN NEW.version = (SELECT current_version FROM documents WHERE id = NEW.document_id) BEGIN
        DELETE FROM document_search WHERE document_id = NEW.document_id;
        INSERT INTO document_search SELECT d.id, d.channel_id, NEW.version, d.title, NEW.content
        FROM documents d WHERE d.id = NEW.document_id;
    END""",
    """CREATE TRIGGER IF NOT EXISTS search_version_delete AFTER DELETE ON document_versions BEGIN
        DELETE FROM document_search WHERE document_id = OLD.document_id AND version = OLD.version;
    END""",
)
