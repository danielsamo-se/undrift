"""Public fictional corpus only. Evaluation labels never enter this module or the DB."""
import hashlib
import json
from pathlib import Path

import fixtures
from publishing import JOURNAL_SCHEMA
from store import SCHEMA_VERSION, Store

PROFILE = 'shiftly-discovery-v1'
SEED_TIME = '2026-11-04T12:00:00+00:00'
CHANNELS = (
    ('website', 'Website', 'public'), ('help-center', 'Help Center', 'public'),
    ('order-email', 'Order emails', 'public'), ('support-snippets', 'Support snippets', 'internal'),
    ('bot-knowledge', 'Bot knowledge', 'internal'), ('legal', 'Legal', 'public'), ('crm', 'CRM notes', 'internal'),
)
DOCUMENTS = (
    ('doc-001', 'website', 'Returns FAQ', 'All customers can return items within 60 days.'),
    ('web-002', 'website', 'Shipping', 'Free shipping on orders above 50 EUR.'),
    ('web-003', 'website', 'Send it back', 'You can send items back within two months.'),
    ('web-004', 'website', 'Durability testing', 'Our accessories are tested for 60 days of daily use.'),
    ('help-001', 'help-center', 'Starting a return', 'Items can be returned within 60 days. Keep your order number handy.'),
    ('help-002', 'help-center', 'Which terms apply?', 'Your return window depends on the offer in your order confirmation. Contact us if unsure.'),
    ('help-003', 'help-center', 'Withdrawal rights', 'Customers have a statutory 14-day withdrawal right.'),
    ('help-004', 'help-center', 'Timing FAQ', 'Some returns take 60 days to complete; your eligibility depends on the original terms.'),
    ('mail-001', 'order-email', 'Order 1001 confirmation', 'Order #1001 was placed before the policy change and retains its promised 60-day return period.'),
    ('mail-002', 'order-email', 'Post-purchase care template', 'All customers can return items within 60 days. Your order number appears in the subject line.'),
    ('mail-003', 'order-email', 'Dispatch notification', 'Your dispatch email includes the tracking link. Tracking history remains available for 60 days.'),
    ('support-001', 'support-snippets', 'Return eligibility reply', 'Returns are accepted within 60 days. Please include your receipt.'),
    ('support-002', 'support-snippets', 'Checking a return window', 'A 60-day window may apply to this return. Check the purchase date before promising eligibility.'),
    ('support-003', 'support-snippets', 'EU withdrawal reply', 'The statutory 14-day EU right of withdrawal is unchanged.'),
    ('support-004', 'support-snippets', 'Delivery reply', 'Free shipping on orders above 50 EUR. Delivery estimates appear at checkout.'),
    ('bot-001', 'bot-knowledge', 'Return assistant answer', 'Customers can return items within two months.'),
    ('bot-002', 'bot-knowledge', 'Order 1042 exception', 'Order #1042 was placed before 2026-11-04 and retains its promised 60-day return period.'),
    ('bot-003', 'bot-knowledge', 'Promotional returns', 'Orders with an eligible promotion may have an extended return window; confirm the promotion terms first.'),
    ('legal-001', 'legal', 'EU consumer withdrawal', 'Customers have a statutory 14-day withdrawal right. This legal right is independent of the voluntary return policy.'),
    ('legal-002', 'legal', 'Transition terms', 'Purchases made before 2026-11-04 keep the return period promised at the time of purchase.'),
    ('crm-001', 'crm', 'Order 0998 care note', 'Order #0998 was placed before the policy change and retains its promised 60-day return period.'),
    ('crm-002', 'crm', 'Order 1099 date check', 'Order #1099 is awaiting a purchase-date check before a return period is confirmed.'),
    ('crm-003', 'crm', 'Spare button request', 'Customer requested a spare button. Customer-care conversations are retained for 60 days.'),
    ('crm-004', 'crm', 'New purchase advice', 'All customers can return items within 60 days. Use the public FAQ when advising on new purchases.'),
)


def manifest():
    return {'profile': PROFILE, 'seed_time': SEED_TIME, 'schema_version': SCHEMA_VERSION,
            'document_version': 1, 'channels': CHANNELS, 'documents': DOCUMENTS,
            'policy_v1': fixtures.SEED_POLICY.model_dump(mode='json'),
            'policy_v2': {**fixtures.SEED_POLICY.model_dump(mode='json'), 'version': 2,
                **{k: str(v) if k == 'effective_from' else v for k, v in fixtures.SHIFTLY_RETURNS_CHANGE.items()}}}


def seed_hash():
    return hashlib.sha256(json.dumps(manifest(), sort_keys=True).encode()).hexdigest()


def create_demo(destination: Path) -> Store:
    """Exclusive new file; deterministic domain state, unique database identity per copy.

    No archive reads, history import, token generation, Cominty call or GitHub call.
    """
    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('xb'):
        pass
    store = Store(destination)
    store.init(seed=False)
    data = manifest()
    with store.write() as c:
        c.execute('INSERT INTO policies VALUES (?,?,?,2)',
                  (fixtures.POLICY_ID, fixtures.POLICY_TITLE, 'voluntary_return'))
        for version in ('policy_v1', 'policy_v2'):
            p = data[version]
            c.execute('INSERT INTO policy_versions VALUES (?,?,?,?,?,?,?)',
                      (p['id'], p['version'], p['voluntary_return_days'], p['effective_from'],
                       p['description'], SEED_TIME, 'seed:' + PROFILE))
        c.executemany('INSERT INTO channels VALUES (?,?,?)', CHANNELS)
        for did, channel, title, content in DOCUMENTS:
            version = data['document_version']
            c.execute('INSERT INTO documents VALUES (?,?,?,?)', (did, channel, title, version))
            for n in range(1, version + 1):
                c.execute('INSERT INTO document_versions VALUES (?,?,?,?,?)',
                          (did, n, content, SEED_TIME, 'seed:' + PROFILE))
        c.executemany('INSERT INTO store_metadata VALUES (?,?)', [
            ('demo_profile', PROFILE), ('seed_sha256', seed_hash()), ('unified_publications', '1'),
            ('requires_scoped_reads', '1'),
        ])
        for statement in JOURNAL_SCHEMA:
            c.execute(statement)
        # Fixed seed event is a fixture operation, never an agent execution or human approval.
        c.execute('INSERT INTO events (ts,type,actor,status,metadata) VALUES (?,?,?,?,?)',
                  (SEED_TIME, 'demo.seeded', 'seed:' + PROFILE, 'ok',
                   json.dumps({'profile': PROFILE, 'seed_sha256': seed_hash(), 'documents': 24,
                               'historical_evidence_imported': False}, sort_keys=True)))
    return store
