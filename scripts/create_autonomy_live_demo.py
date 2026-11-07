"""Create the isolated, explicitly labelled live autonomy scenario (data/autonomy-live-v1.db).

A NEW three-document corpus, separate from the 24-document discovery scenario (which is never
copied, migrated or edited). Every return-policy fact the documents depend on is in the
authoritative policy; no purchase dates, offers or promotions are needed. Help Center
delegation (bounded_auto, at most one application per run) is configured by the authenticated
local reviewer BEFORE any run, so it is pinned to the run at open time.

No Cominty, MCP, GitHub or network call is made here.
"""
import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SCENARIO = 'autonomy-live-v1'
NOTICE = ('Isolated live autonomy scenario: three fictional Shiftly documents, Help Center delegated '
          'before the run. Separate from the 24-document discovery scenario.')
CHANNELS = (('help-center', 'Help Center', 'public'), ('website', 'Website', 'public'))
DOCUMENTS = (
    ('help-101', 'help-center', 'Starting a return', 'Items can be returned within 60 days. Keep your receipt handy.'),
    ('help-102', 'help-center', 'Withdrawal rights', 'Customers have a statutory 14-day withdrawal right.'),
    ('web-101', 'website', 'Shipping', 'Free shipping on orders above 50 EUR.'),
)
DELEGATION_NOTE = ('Live autonomy scenario: Help Center may apply at most one verified single-sentence '
                   'return-window correction per run.')


def manifest():
    import discovery_seed
    base = discovery_seed.manifest()
    return {'scenario': SCENARIO, 'profile': discovery_seed.PROFILE, 'seed_time': discovery_seed.SEED_TIME,
            'schema_version': base['schema_version'], 'document_version': 1, 'channels': CHANNELS,
            'documents': DOCUMENTS, 'policy_v1': base['policy_v1'], 'policy_v2': base['policy_v2']}


def seed_hash():
    return hashlib.sha256(json.dumps(manifest(), sort_keys=True).encode()).hexdigest()


def create(destination: Path, *, token: str):
    import autonomy
    import discovery_seed
    import fixtures
    from publishing import JOURNAL_SCHEMA
    from store import Store

    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('xb'):
        pass
    store = Store(destination)
    store.init(seed=False)
    data, seed_time, origin = manifest(), discovery_seed.SEED_TIME, 'seed:' + SCENARIO
    with store.write() as c:
        c.execute('INSERT INTO policies VALUES (?,?,?,2)', (fixtures.POLICY_ID, fixtures.POLICY_TITLE, 'voluntary_return'))
        for key in ('policy_v1', 'policy_v2'):
            p = data[key]
            c.execute('INSERT INTO policy_versions VALUES (?,?,?,?,?,?,?)',
                      (p['id'], p['version'], p['voluntary_return_days'], p['effective_from'], p['description'],
                       seed_time, origin))
        c.executemany('INSERT INTO channels VALUES (?,?,?)', CHANNELS)
        for did, channel, title, content in DOCUMENTS:
            c.execute('INSERT INTO documents VALUES (?,?,?,1)', (did, channel, title))
            c.execute('INSERT INTO document_versions VALUES (?,?,?,?,?)', (did, 1, content, seed_time, origin))
        # demo_profile is the discovery-mode gate checked by the runner and MCP readiness;
        # scenario/operational_profile/seed_sha256 identify THIS corpus.
        c.executemany('INSERT INTO store_metadata VALUES (?,?)', [
            ('demo_profile', discovery_seed.PROFILE), ('seed_sha256', seed_hash()), ('unified_publications', '1'),
            ('requires_scoped_reads', '1'), ('scenario', SCENARIO), ('operational_profile', SCENARIO),
            ('scenario_notice', NOTICE)])
        for statement in JOURNAL_SCHEMA:
            c.execute(statement)
        c.execute('INSERT INTO events (ts,type,actor,status,metadata) VALUES (?,?,?,?,?)',
                  (seed_time, 'demo.seeded', origin, 'ok', json.dumps(
                      {'profile': discovery_seed.PROFILE, 'scenario': SCENARIO, 'seed_sha256': seed_hash(),
                       'documents': len(DOCUMENTS), 'historical_evidence_imported': False}, sort_keys=True)))
    delegation = autonomy.configure(store, 'help-center', token=token, expected_version=0, mode='bounded_auto',
                                    max_per_run=1, note=DELEGATION_NOTE)
    return store, delegation


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--target', type=Path, default=ROOT / 'data' / 'autonomy-live-v1.db')
    args = parser.parse_args()
    import config
    config.load_env()
    token = os.environ.get('UNDRIFT_REVIEW_TOKEN', '').strip()
    if not token:
        sys.exit('UNDRIFT_REVIEW_TOKEN is required to record the reviewer delegation')
    if args.target.exists():
        sys.exit(f'{args.target.name} already exists; refusing to overwrite')
    store, delegation = create(args.target, token=token)
    record = {'database': str(args.target.resolve()), 'database_id': store.identity(), 'scenario': SCENARIO,
              'seed_sha256': seed_hash(), 'documents': [d[0] for d in DOCUMENTS],
              'delegation': {k: delegation[k] for k in ('channel_id', 'version', 'mode', 'max_per_run',
                                                        'updated_by', 'updated_at', 'note')}}
    print(json.dumps(record, indent=2))


if __name__ == '__main__':
    main()
