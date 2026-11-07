"""Prepare, verify or (with explicit approval) create the separate Undrift Guard agent.

    python scripts/guard_agent.py plan            # local only: exact payload, no network
    python scripts/guard_agent.py check           # free: verify UNDRIFT_GUARD_AGENT_ID config
    python scripts/guard_agent.py create --apply  # REMOTE WRITE: creates a new private agent

The Guard has no tools: every capability is disabled; Undrift supplies the verified case in
the message. The Impact Agent and its MCP integration are never touched.
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

NAME = 'Undrift Guard'
DESCRIPTION = 'Fail-closed validator for outgoing Shiftly policy and support responses (no tools).'
MODEL_ID = os.environ.get('UNDRIFT_GUARD_MODEL_ID', 'CqMYxVBlPnPf')  # Claude Sonnet 5.5 (High)
EXPECTED_MODEL = 'Claude Sonnet 5.5 (High)'
INSTRUCTIONS = ROOT / 'agents' / 'guard.md'
CAPABILITIES = ('mcp', 'web', 'indexed_documents', 'skills', 'image_generation', 'video_generation',
                'music_generation', 'memory')


def instructions() -> str:
    return INSTRUCTIONS.read_text(encoding='utf-8').replace('\r\n', '\n').strip()


def capabilities():
    import cominty_sdk as sdk
    names = {'mcp': 'McpCapability', 'web': 'WebCapability', 'indexed_documents': 'IndexedDocumentsCapability',
             'skills': 'SkillsCapability', 'image_generation': 'ImageGenerationCapability',
             'video_generation': 'VideoGenerationCapability', 'music_generation': 'MusicGenerationCapability',
             'memory': 'MemoryCapability'}
    return sdk.AgentCapabilities(**{k: getattr(sdk, v)(activation='never') for k, v in names.items()})


def plan() -> dict:
    text = instructions()
    return {'action': 'client.agents.create', 'name': NAME, 'description': DESCRIPTION, 'scope': 'private',
            'model_ids': [MODEL_ID], 'expected_model': EXPECTED_MODEL,
            'capabilities': {k: 'never' for k in CAPABILITIES}, 'allow_execution_options_override': False,
            'instructions_file': str(INSTRUCTIONS.relative_to(ROOT)), 'instructions_chars': len(text),
            'instructions_sha256': hashlib.sha256(text.encode()).hexdigest()}


async def check() -> int:
    from cominty_sdk import AsyncCominty
    agent_id = os.environ.get('UNDRIFT_GUARD_AGENT_ID', '').strip()
    if not agent_id:
        print('UNDRIFT_GUARD_AGENT_ID is not set: no Guard agent configured.')
        return 1
    async with AsyncCominty() as client:
        agent = await client.agents.get(agent_id)
    caps = agent.capabilities
    tools_off = all(getattr(getattr(caps, k, None), 'activation', 'never') == 'never' for k in CAPABILITIES) if caps else False
    models = [m.name for m in agent.models]
    remote = (agent.instructions or '').replace('\r\n', '\n').strip()
    results = {'scope_private': agent.scope == 'private', 'all_tools_disabled': tools_off,
               'model_chain': models == [EXPECTED_MODEL], 'instructions_match': remote == instructions()}
    for k, v in results.items():
        print(f"[{'PASS' if v else 'FAIL'}] {k}")
    return 0 if all(results.values()) else 1


async def create() -> int:
    from cominty_sdk import AsyncCominty
    async with AsyncCominty() as client:
        agent = await client.agents.create(name=NAME, description=DESCRIPTION, instructions=instructions(),
                                           scope='private', capabilities=capabilities(),
                                           allow_execution_options_override=False, model_ids=[MODEL_ID])
    print(f'Created private Guard agent id={agent.id}. Store it as UNDRIFT_GUARD_AGENT_ID in .env.')
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['plan', 'check', 'create'])
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
    return asyncio.run(check() if args.command == 'check' else create())


if __name__ == '__main__':
    sys.exit(main())
