"""Loopback service for an explicitly selected fresh evaluation database."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import config
from store import Store


def series_paths(active):
    # Legacy service configs remain readable, but new studies have explicit IDs.
    series = active.get('series_id', 'reliability-v1')
    if not re.fullmatch(r'reliability-v[1-9][0-9]*', series):
        raise ValueError('invalid evaluation series')
    database = (ROOT / active['database']).resolve()
    if not database.is_relative_to((ROOT / 'data' / series).resolve()) or not database.is_file():
        raise ValueError('evaluation database is outside the selected series')
    return database, ROOT / 'evaluations' / (series + '-results.json')

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('service', choices=['api','mcp'])
    parser.add_argument('--config', required=True, type=Path)
    args = parser.parse_args()
    config.load_env()
    active = json.loads(args.config.read_text(encoding='utf-8'))
    database, report = series_paths(active)
    store = Store(database)
    assert store.identity() == active['database_id']
    assert store.metadata('seed_sha256') == active['seed_sha256']
    os.environ.update(UNDRIFT_DB=str(database), MCP_PORT='8001', UNDRIFT_API_PORT='8401',
        MCP_PUBLIC_HOST=active['public_host'], UNDRIFT_MCP_CONNECTION='undrift_mcp_ngrok',
        UNDRIFT_EXPECTED_MODEL='Claude Sonnet 5.5 (High)', UNDRIFT_MCP_DISCOVERY='1',
        UNDRIFT_MCP_FINDINGS='1', UNDRIFT_MCP_FINDINGS_PUBLIC='1', UNDRIFT_MCP_WRITE_TOOLS='0',
        UNDRIFT_GITHUB_REPOSITORY='', UNDRIFT_PUBLISH_CREDENTIAL_FILE='', UNDRIFT_PUBLISH_DB=str(database),
        UNDRIFT_RELIABILITY_REPORT=str(report))
    ui = (ROOT / active['ui_directory']).resolve()
    assert ui.is_relative_to((ROOT / '.test-tmp').resolve()) and (ui / 'index.html').is_file()
    runtime = {'pid': os.getpid(), 'service': args.service, **active,
               'source_sha256':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
                                ['api.py','server.py','clarification.py','discovery.py','workflow.py','reliability.py']},
               'ui_index_sha256':hashlib.sha256((ui/'index.html').read_bytes()).hexdigest()}
    (args.config.parent / f'{args.service}-runtime.json').write_text(json.dumps(runtime, indent=2), encoding='utf-8')
    if args.service == 'mcp':
        import server
        server.main()
    else:
        import api
        import uvicorn
        uvicorn.run(api.build_app(store, ui_dir=ui), host='127.0.0.1', port=8401,
                    access_log=False, proxy_headers=False, server_header=False)

if __name__ == '__main__':
    try: main()
    except BaseException as exc:
        print('Evaluation service stopped:', type(exc).__name__)
        raise SystemExit(1) from None
