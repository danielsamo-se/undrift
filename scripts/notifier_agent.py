"""Prepare, verify or (with explicit approval) create the Undrift Notifier agent for Slack.

    python scripts/notifier_agent.py plan            # local only: exact payload, no network
    python scripts/notifier_agent.py status          # free: Cominty Slack connector status (no URLs)
    python scripts/notifier_agent.py check           # free: verify UNDRIFT_NOTIFY_AGENT_ID config
    python scripts/notifier_agent.py create --apply  # REMOTE WRITE: creates a new private agent

The Notifier may use exactly one MCP connection (Cominty's official Slack connector, on request);
every other capability is disabled. Impact and Guard agents are never touched.
"""
import argparse
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import config  # noqa: E402

NAME = 'Undrift Notifier'
DESCRIPTION = 'Posts prepared Undrift review notifications to one authorized Slack channel (Slack connector only).'
MODEL_ID = os.environ.get('UNDRIFT_NOTIFY_MODEL_ID', 'CqMYxVBlPnPf')  # Claude Sonnet 5.5 (High)
EXPECTED_MODEL = 'Claude Sonnet 5.5 (High)'
INSTRUCTIONS = ROOT / 'agents' / 'notifier.md'
OFF = ('web', 'indexed_documents', 'skills', 'image_generation', 'video_generation', 'music_generation', 'memory')


def connection() -> str:
    return os.environ.get('UNDRIFT_SLACK_CONNECTION', 'slack_official').strip() or 'slack_official'


def instructions() -> str:
    return INSTRUCTIONS.read_text(encoding='utf-8').replace('\r\n', '\n').strip()


def capabilities():
    import cominty_sdk as sdk
    names = {'web': 'WebCapability', 'indexed_documents': 'IndexedDocumentsCapability', 'skills': 'SkillsCapability',
             'image_generation': 'ImageGenerationCapability', 'video_generation': 'VideoGenerationCapability',
             'music_generation': 'MusicGenerationCapability', 'memory': 'MemoryCapability'}
    return sdk.AgentCapabilities(mcp=sdk.McpCapability(activation='on_request', connections=[connection()]),
                                 **{k: getattr(sdk, v)(activation='never') for k, v in names.items()})


def plan() -> dict:
    text = instructions()
    return {'action': 'client.agents.create', 'name': NAME, 'description': DESCRIPTION, 'scope': 'private',
            'model_ids': [MODEL_ID], 'expected_model': EXPECTED_MODEL,
            'capabilities': {'mcp': {'activation': 'on_request', 'connections': [connection()]},
                             **{k: 'never' for k in OFF}},
            'allow_execution_options_override': False, 'instructions_file': str(INSTRUCTIONS.relative_to(ROOT)),
            'instructions_chars': len(text), 'instructions_sha256': hashlib.sha256(text.encode()).hexdigest()}


async def status() -> int:
    from cominty_sdk import AsyncCominty
    async with AsyncCominty() as client:
        servers = await client.mcp.list(enabled=None)
    for s in servers:
        if 'slack' in s.name:
            print(json.dumps({k: getattr(s, k) for k in ('name', 'title', 'is_enabled', 'requires_oauth_connect',
                                                         'connection_status')}))
    return 0


async def check() -> int:
    from cominty_sdk import AsyncCominty
    agent_id = os.environ.get('UNDRIFT_NOTIFY_AGENT_ID', '').strip()
    if not agent_id:
        print('UNDRIFT_NOTIFY_AGENT_ID is not set: no Notifier agent configured.')
        return 1
    async with AsyncCominty() as client:
        agent = await client.agents.get(agent_id)
        servers = {s.name: s for s in await client.mcp.list(enabled=None)}
    caps = agent.capabilities
    mcp = getattr(caps, 'mcp', None)
    slack = servers.get(connection())
    results = {
        'scope_private': agent.scope == 'private',
        'other_tools_disabled': all(getattr(getattr(caps, k, None), 'activation', None) == 'never' for k in OFF),
        'mcp_only_slack': bool(mcp) and mcp.connections == [connection()],
        'model_chain': [m.name for m in agent.models] == [EXPECTED_MODEL],
        'instructions_match': (agent.instructions or '').replace('\r\n', '\n').strip() == instructions(),
        'slack_connector_enabled': bool(slack and slack.is_enabled),
    }
    for k, v in results.items():
        print(f"[{'PASS' if v else 'FAIL'}] {k}")
    return 0 if all(results.values()) else 1


async def create() -> int:
    from cominty_sdk import AsyncCominty
    async with AsyncCominty() as client:
        existing = [a for a in await client.agents.list() if a.name == NAME]
        if existing:
            print(f'Refusing: a {NAME!r} agent already exists (id={existing[0].id}); verify and reuse it.')
            return 2
        agent = await client.agents.create(name=NAME, description=DESCRIPTION, instructions=instructions(),
                                           scope='private', capabilities=capabilities(),
                                           allow_execution_options_override=False, model_ids=[MODEL_ID])
    print(f'Created private Notifier agent id={agent.id}. Store it as UNDRIFT_NOTIFY_AGENT_ID in .env.')
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['plan', 'status', 'check', 'create'])
    parser.add_argument('--apply', action='store_true', help='required for create (remote write)')
    args = parser.parse_args()
    config.load_env()
    if args.command == 'plan':
        print(json.dumps(plan(), indent=2))
        return 0
    if args.command == 'create' and not args.apply:
        print('Refusing: creating a remote agent requires --apply (and prior approval).')
        print(json.dumps(plan(), indent=2))
        return 2
    return asyncio.run({'status': status, 'check': check, 'create': create}[args.command]())


if __name__ == '__main__':
    sys.exit(main())
