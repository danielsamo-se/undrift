"""Cominty Managed Agent impact analysis + correction drafting -> pending proposals.

    python run_impact.py          # free checks only (no agent run, no credit spent)
    python run_impact.py --run    # ONE paid agent run

Preconditions checked before a paid run: the free spike checks pass, the agent's remote
instructions equal agents/patch.md, and the local policy has a newer version than its seed.
A run is opened locally first (workflow.open_run): it gets a run ID and a short-lived run token
that the agent passes to the record_finding MCP tool, one finding per document. The final JSON
reply is still parsed as a compatibility fallback and cross-checked against recorded findings;
corrections become pending_review proposals through workflow.submit_proposal. Nothing is
approved or applied. The run token is redacted from everything this script prints or saves.
"""

from __future__ import annotations

import argparse
import hashlib
import asyncio
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cominty_sdk import (
    AgentCapabilities,
    AsyncCominty,
    ComintyError,
    ImageGenerationCapability,
    IndexedDocumentsCapability,
    McpCapability,
    MemoryCapability,
    SkillsCapability,
    WebCapability,
    VideoGenerationCapability,
    MusicGenerationCapability,
    events,
)

import config
import fixtures
import workflow
from spike import free_checks, out, require_env
from store import Store

ROOT = Path(__file__).resolve().parent
INSTRUCTIONS = ROOT / "agents" / "patch.md"
DEFAULT_MAX_STEPS = 8


def build_prompt(policy_id: str, document_ids: list[str], run_id: str, run_token: str,
                 discovery: bool = False) -> str:
    if discovery:
        return (
            f'Autonomous discovery run.\nRun ID: {run_id}\nRun token: {run_token}\n\n'
            f'Retrieve policy {policy_id!r}. Discover the available channels and document counts with list_channels. '
            'Use search_documents (including empty-query paginated browsing) and similar_documents to inspect the '
            'entire authorized corpus, without asking the user for document IDs. Search results are leads, not '
            'verified retrieval. Before catalog browsing, perform at least three distinct non-empty searches '
            'using different formulations, including one without numbers, and retrieve relevant search leads. '
            'Use similarity only when it adds useful leads. Legal and CRM documents are classification-only; '
            'never submit proposed_content for those channels. UNKNOWN means a return-policy conclusion '
            'depends on unresolved facts; absence of a contradiction does not make a document UNRELATED. '
            'Pass run_id and run_token to EVERY discovery, retrieval and finding call, '
            'including through the integration subagent. Retrieve each document in full with get_document '
            'before using its exact quoted evidence. Record each finding immediately with record_finding. '
            'An optional proposed_content is allowed only for OUTDATED findings and must preserve all protected '
            'and unrelated text. Use UNKNOWN for genuinely ambiguous scope. End with the final JSON contract '
            'in your instructions, preserving accepted findings and corrections exactly. Never approve, apply '
            'or publish. Never print the token.'
        )
    return (
        f"Analysis run.\nRun ID: {run_id}\nRun token: {run_token}\n\n"
        f"Use the Undrift MCP integration (through its integration subagent if the tools are not "
        f"directly available) to retrieve policy '{policy_id}' with get_policy and each of these "
        f"documents with get_document: {', '.join(document_ids)}. Have every tool result returned "
        "complete and unmodified, including the policy version and effective date and each "
        "document's version and full content. Pass run_id and run_token to EVERY get_policy "
        "and get_document call, including calls through the integration subagent. "
        "Unscoped reads do not verify retrieval. Classify every document against the current "
        "policy and record one finding per document with record_finding, passing the run ID and "
        "run token above exactly. Draft a full corrected text for every OUTDATED document. Your "
        "findings and corrections are for human review: do not claim anything was changed. End "
        "with the JSON block described in your instructions."
    )


async def instructions_match(client: AsyncCominty, agent_id: str, path: Path = INSTRUCTIONS) -> bool:
    remote = ((await client.agents.get(agent_id)).instructions or "").replace("\r\n", "\n").strip()
    local = path.read_text(encoding="utf-8").replace("\r\n", "\n").strip()
    match = remote == local
    out(f"instructions remote sha={config.fingerprint(remote)} local {path.name} "
        f"sha={config.fingerprint(local)}: {'MATCH' if match else 'MISMATCH'}")
    return match


def local_checks(store: Store) -> bool:
    policy = store.policy(fixtures.POLICY_ID)
    if policy is None or policy.version < 2:
        out("local        policy has no change yet: run `python cli.py change-policy --shiftly-30`")
        return False
    candidates = workflow.detect_candidates(store, policy.id)
    out(f"local        {policy.id} v{policy.version} ({policy.voluntary_return_days} days); "
        f"scan candidates={[c.document_id for c in candidates]} "
        f"actionable={[c.document_id for c in candidates if c.actionable]}")
    return True


async def mcp_ready(store: Store, discovery: bool = False) -> bool:
    """Verify the actual endpoint, scoped schemas, and shared database before spending."""
    from mcp import Client
    port = int(os.environ.get('MCP_PORT', config.DEFAULT_PORT))
    try:
        host = config.public_host()
        origin = f'https://{host}' if host else f'http://127.0.0.1:{port}'
        async with Client(origin + config.mcp_path(config.path_secret()), read_timeout_seconds=15) as client:
            health = await client.call_tool('health_check', {})
            available = {t.name: t for t in (await client.list_tools()).tools}
            scoped = all(name in available and {'run_id', 'run_token'} <= set(
                available[name].input_schema.get('properties', {})) for name in ('get_policy', 'get_document'))
            shared = not health.is_error and (health.structured_content or {}).get('database_id') == store.identity()
            ready = shared and scoped and 'record_finding' in available
            if discovery:
                strategy = (health.structured_content or {}).get('discovery_strategy')
                ready = ready and strategy == 'search_first_v1' and store.metadata('demo_profile') == 'shiftly-discovery-v1' and all(
                    name in available and {'run_id', 'run_token'} <= set(available[name].input_schema.get('required', []))
                    for name in ('list_channels', 'search_documents', 'similar_documents'))
                out(f'Discovery endpoint scoped tools and unified profile: {ready}; strategy={strategy}')
            out(f"MCP endpoint record_finding {'available' if 'record_finding' in available else 'unavailable'}; "
                f"scoped_reads={scoped}; shared_database={shared}")
            return ready
    except Exception as exc:
        out(f'MCP endpoint unavailable ({type(exc).__name__})')
        return False


async def paid_run(client: AsyncCominty, store: Store, agent_id: str, connection: str,
                   max_steps: int, discovery: bool = False) -> int:
    actor = f'agent:cominty:{agent_id}'
    document_ids = store.document_ids()
    agent_run, run_token = workflow.open_run(store, fixtures.POLICY_ID, document_ids,
        actor=actor, agent_id=agent_id, ttl=workflow.RUN_TTL,
        metadata={'max_steps': max_steps,
            **({'discovery': True, 'discovery_strategy': 'search_first_v1', 'seed_sha256': store.metadata('seed_sha256'),
                'instructions_sha256': hashlib.sha256((ROOT / 'agents/discovery.md').read_bytes()).hexdigest(),
                'expected_model': os.environ.get('UNDRIFT_EXPECTED_MODEL')} if discovery else {})})

    async def heartbeat() -> None:
        while True:
            await asyncio.sleep(20)
            await asyncio.to_thread(workflow.heartbeat_run, store, agent_run.id)

    task = asyncio.create_task(heartbeat())
    work = asyncio.create_task(_paid_run(client, store, agent_id, connection, max_steps,
                                        agent_run.id, run_token, document_ids, actor,
                                        **({'discovery': True} if discovery else {})))
    try:
        # A failed lease renewal cancels the local run instead of silently losing ownership.
        done, _ = await asyncio.wait({task, work}, timeout=workflow.RUN_TTL.total_seconds(),
                                     return_when=asyncio.FIRST_COMPLETED)
        if not done:
            raise TimeoutError('analysis lifetime exceeded')
        if work in done:
            return await work
        if task in done:
            current = store.run(agent_run.id)
            if current.status == 'completed' or (current.status == 'incomplete'
                    and current.metadata.get('finalization') == 'per_document_v1'):
                return await work
        if task in done:
            await task
            raise RuntimeError('runner heartbeat stopped')
        return await work
    except BaseException as exc:
        store.reap_runs()
        current = store.run(agent_run.id)
        if current and current.status == 'open':
            workflow.close_run(store, agent_run.id,
                status='interrupted' if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt))
                    else 'expired' if isinstance(exc, TimeoutError) else 'failed',
                actor=actor, error=f'run aborted: {type(exc).__name__}')
        raise
    finally:
        for pending in (task, work):
            if not pending.done():
                pending.cancel()
        await asyncio.gather(task, work, return_exceptions=True)


async def _paid_run(client: AsyncCominty, store: Store, agent_id: str, connection: str,
                    max_steps: int, run_id: str, run_token: str, document_ids: list[str],
                    actor: str, discovery: bool = False) -> int:
    def scrub(text: str) -> str:
        return config.redact(text, run_token)

    prompt = build_prompt(fixtures.POLICY_ID, document_ids, run_id, run_token, discovery=discovery)
    record: dict[str, Any] = {'run_id': run_id, 'started_at': datetime.now(UTC).isoformat(),
                              'prompt': scrub(prompt), 'events': []}
    out(f'run          {run_id} opened (token issued, not shown)')
    t0 = time.monotonic()
    path = ROOT / 'runs' / f'impact_{run_id}.json'
    path.parent.mkdir(exist_ok=True)

    def save() -> None:
        temporary = path.with_suffix('.tmp')
        temporary.write_text(scrub(json.dumps(record, indent=2, default=str)), encoding='utf-8')
        temporary.replace(path)

    run = None
    terminal_received = False
    save()
    try:
        run = await client.chat.start(
            agent_id=agent_id, message=prompt, name='undrift-impact', max_steps=max_steps,
            thread_capabilities=AgentCapabilities(
                mcp=McpCapability(activation='always', connections=[connection]),
                web=WebCapability(activation='never'),
                indexed_documents=IndexedDocumentsCapability(activation='never'),
                skills=SkillsCapability(activation='never'),
                image_generation=ImageGenerationCapability(activation='never'),
                video_generation=VideoGenerationCapability(activation='never'),
                music_generation=MusicGenerationCapability(activation='never'),
                memory=MemoryCapability(activation='never')))
        provenance = {'source': 'cominty', 'agent_id': agent_id, 'run_id': run_id,
                      'thread_id': str(run.thread.id), 'message_id': str(run.message_id)}
        record['provenance'] = provenance
        save()
        workflow.heartbeat_run(store, run_id, provenance=provenance)
        cost = None
        async with run:
            async for event in run:
                record['events'].append(json.loads(scrub(event.model_dump_json())))
                save()
                if isinstance(event, events.Result):
                    cost = json.loads(event.data.cost.model_dump_json())
        message = await run.result()
        terminal_received = True
        reply = message.content or ''
        retrieval = workflow.retrieval_status(store, store.run(run_id))
        metadata = {'execution_status': message.status.value, 'cost': cost,
                    'duration_s': round(time.monotonic() - t0, 1), 'retrieval': retrieval}
        record.update(provenance=provenance, reply=scrub(reply), cost=cost,
                      execution_status=message.status.value, retrieval=retrieval)
        # Capture paid evidence before creating proposals. Product state does not depend on this file.
        save()
        summary = workflow.ingest_agent_reply(store, reply, actor=actor, provenance=provenance,
            run_id=run_id, execution_status=message.status.value, metadata=metadata)
        state = store.run(run_id)
        record.update(summary=summary, status=state.status,
                      findings=[f.model_dump(mode='json') for f in store.findings(run_id=run_id)],
                      finished_at=datetime.now(UTC).isoformat())
        try:
            save()
        except OSError:
            out('Evidence summary could not be updated; canonical results are persisted in SQLite.')
        out(scrub(reply))
        out(f'run {run_id}: {state.status}; findings={summary["classifications"]}; '
            f'proposals={summary["proposals"]}; errors={summary["errors"]}')
        out(f'Evidence saved to {path.relative_to(ROOT)}')
        return 0 if state.status == 'completed' and not summary['errors'] else 1
    except BaseException as exc:
        record.update(error=type(exc).__name__, finished_at=datetime.now(UTC).isoformat())
        if run is not None and not terminal_received:
            record['cancellation'] = 'attempted'
            try:
                await asyncio.wait_for(client.chat.cancel(run.message_id), timeout=10)
                record['cancellation'] = 'requested'
            except Exception as cancel_error:
                record['cancellation'] = type(cancel_error).__name__
        try:
            save()
        except OSError:
            out('Partial evidence could not be saved; inspect the remote run before retrying.')
        raise


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true", help="start ONE paid agent run")
    parser.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS)
    parser.add_argument('--discovery', action='store_true', help='Use scoped discovery and local agents/discovery.md; requires explicit isolated DB')
    args = parser.parse_args()

    config.load_env()
    env = require_env("COMINTY_API_KEY", "COMINTY_USER_ID", "COMINTY_AGENT_ID")
    connection = os.environ.get("UNDRIFT_MCP_CONNECTION", "undrift").strip()
    store = Store()
    if args.discovery:
        if not os.environ.get('UNDRIFT_DB') or store.path.resolve() == (ROOT / 'data/undrift.db').resolve():
            raise SystemExit('Discovery requires an explicit isolated unified demo database')
        if not store.path.is_file() or store.metadata('demo_profile') != 'shiftly-discovery-v1':
            raise SystemExit('Create a fresh unified discovery database first; archives must not be migrated')
    store.init()
    try:
        async with AsyncCominty() as client:
            ready, _ = await free_checks(client, env["COMINTY_AGENT_ID"], connection)
            ready = await instructions_match(client, env["COMINTY_AGENT_ID"], ROOT / 'agents/discovery.md' if args.discovery else INSTRUCTIONS) and ready
            ready = local_checks(store) and ready
            ready = await mcp_ready(store, discovery=args.discovery) and ready
            if not args.run:
                out("\nFree checks done. No agent run was started (no credit spent).")
                return 0 if ready else 1
            if not ready:
                out("\nRefusing to start a paid run: checks failed.")
                return 1
            out(f"\nStarting ONE paid run (max_steps={args.max_steps}).\n")
            return await paid_run(client, store, env["COMINTY_AGENT_ID"], connection,
                                  args.max_steps, **({'discovery': True} if args.discovery else {}))
    except ComintyError as exc:
        out(f"Cominty error: {type(exc).__name__}: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
