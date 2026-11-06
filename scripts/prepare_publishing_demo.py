"""Copy a real proposal into a NEW local publishing demo, without changing its source.

No fake agent execution or auto-approval. All migrations occur only on the new copy.
"""
import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from store import Store


def prepare(source: Path, destination: Path):
    source, destination = source.resolve(), destination.resolve()
    if not source.is_file() or source == destination or destination.exists():
        raise SystemExit('Source must exist and destination must be a new, separate database')
    destination.parent.mkdir(parents=True, exist_ok=True)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    destination.touch(exist_ok=False)
    with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as src:
        with sqlite3.connect(destination) as dst:
            src.backup(dst)
    store = Store(destination)
    store.init(seed=False)
    pending = [p for p in store.proposals() if p.status == 'pending_review']
    if not pending:
        raise SystemExit('Copy prepared, but no pending proposal exists. Do not invent an agent result.')
    original = (ROOT / 'shiftly/content/returns.txt').read_text(encoding='utf-8')
    matching = [p for p in pending if p.document_id == 'doc-001' and p.original_content == original]
    if not matching:
        raise SystemExit('Copy prepared, but pending proposal does not match the storefront source')
    if hashlib.sha256(source.read_bytes()).hexdigest() != before:
        raise SystemExit('Source changed during preparation; inspect its running writer before continuing')
    return {'database': str(destination), 'proposal_ids': [p.id for p in matching],
            'source_unchanged': True, 'approval': 'pending human review'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, default=ROOT / 'data/undrift.db')
    parser.add_argument('--destination', type=Path, default=ROOT / 'data' /
        ('publishing-demo-' + datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ') + '.db'))
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.destination), indent=2))
