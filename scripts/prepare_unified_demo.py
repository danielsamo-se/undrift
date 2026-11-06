"""Create a fresh unified demo. Refuses existing destinations; never reads archives."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from discovery_seed import create_demo


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', type=Path, default=ROOT / 'data/unified-demo-v1.db')
    args = parser.parse_args()
    try:
        store = create_demo(args.database)
    except FileExistsError:
        raise SystemExit('Destination already exists. Choose a NEW filename; no reset was performed.') from None
    print(json.dumps({'database': str(store.path), 'database_id': store.identity(),
        'seed_sha256': store.metadata('seed_sha256'), 'documents': len(store.documents()),
        'channels': len(store.channels()), 'historical_evidence_imported': False,
        'runs': 0, 'findings': 0, 'proposals': 0, 'publication_jobs': 0}, indent=2))
