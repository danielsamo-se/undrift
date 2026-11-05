"""Cominty Managed Agent impact analysis + correction drafting -> pending proposals.

    python run_impact.py          # free checks only (no agent run, no credit spent)
    python run_impact.py --run    # ONE paid agent run

Preconditions checked before a paid run: the free spike checks pass, the agent's remote
instructions equal agents/patch.md, and the local policy has a newer version than its seed.
The agent's reply is parsed locally; corrections become pending_review proposals through
workflow.submit_proposal (same validation as every other path). Nothing is approved or applied.
"""

from __future__ import annotations

import argparse
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
    events,
)

import config
import fixtures
import workflow
from spike import free_checks, out, require_env
from store import Store

ROOT = Path(__file__).resolve().parent
INSTRUCTIONS = ROOT / "agents" / "patch.md"
DEFAULT_MAX_STEPS = 6


def build_prompt(policy_id: str, document_ids: list[str]) -> str:
    return (
        f"Use the Undrift MCP integration (through its integration subagent if the tools are not "
        f"directly available) to retrieve policy '{policy_id}' with get_policy and each of these "
        f"documents with get_document: {', '.join(document_ids)}. Have every tool result returned "
        "complete and unmodified, including the policy version and effective date and each "
        "document's version and full content. Classify every document against the current "
        "policy, quote the evidence, and draft a full corrected text for every OUTDATED document. "
        "Your corrections are proposals for human review: do not claim anything was changed. End "
        "with the JSON block described in your instructions."
    )


def retrieval_check(calls: list[Any], policy_id: str, document_ids: list[str]) -> dict[str, Any]:
    """What the MCP server actually served during the run, whoever (agent or subagent) asked."""
    served = [e.metadata for e in calls if e.status == "ok"]
    policy = sorted({m.get("version") for m in served
                     if m.get("tool") == "get_policy" and m.get("policy_id") == policy_id})
    docs = {d: sorted({m.get("version") for m in served
                       if m.get("tool") == "get_document" and m.get("document_id") == d})
            for d in document_ids}
    missing = ([f"policy:{policy_id}"] if not policy else []) + [d for d, v in docs.items() if not v]
    return {"policy_versions": policy, "document_versions": docs, "missing": missing,
            "complete": not missing}


async def instructions_match(client: AsyncCominty, agent_id: str) -> bool:
    remote = ((await client.agents.get(agent_id)).instructions or "").replace("\r\n", "\n").strip()
    local = INSTRUCTIONS.read_text(encoding="utf-8").replace("\r\n", "\n").strip()
    match = remote == local
    out(f"instructions remote sha={config.fingerprint(remote)} local agents/patch.md "
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


async def paid_run(client: AsyncCominty, store: Store, agent_id: str, connection: str,
                   max_steps: int) -> int:
    prompt = build_prompt(fixtures.POLICY_ID, store.document_ids())
    actor = f"agent:cominty:{agent_id}"
    started_at = datetime.now(UTC)
    first_seq = store.record_event("agent.run_started", actor=actor, metadata={
        "agent_id": agent_id, "max_steps": max_steps, "prompt_sha256": config.fingerprint(prompt)})
    record: dict[str, Any] = {"started_at": started_at.isoformat(), "prompt": prompt, "events": []}
    t0 = time.monotonic()
    run = await client.chat.start(
        agent_id=agent_id, message=prompt, name="undrift-impact", max_steps=max_steps,
        thread_capabilities=AgentCapabilities(
            mcp=McpCapability(activation="always", connections=[connection]),
            web=WebCapability(activation="never"),
            indexed_documents=IndexedDocumentsCapability(activation="never"),
            skills=SkillsCapability(activation="never"),
            image_generation=ImageGenerationCapability(activation="never"),
            memory=MemoryCapability(activation="never"),
        ),
    )
    provenance = {"source": "cominty", "agent_id": agent_id, "thread_id": str(run.thread.id),
                  "message_id": str(run.message_id)}
    out(f"thread={run.thread.id} message={run.message_id}")
    cost: dict[str, Any] | None = None
    async with run:
        async for event in run:
            record["events"].append(json.loads(config.redact(event.model_dump_json())))
            dt = time.monotonic() - t0
            if isinstance(event, events.ToolCall):
                out(f"{dt:6.1f}s TOOL {event.status:<7} {event.data.name}")
            elif isinstance(event, events.LlmStep):
                out(f"{dt:6.1f}s LLM  {event.status:<7} model={event.data.model!r}")
            elif isinstance(event, events.Result):
                cost = json.loads(event.data.cost.model_dump_json())
                out(f"{dt:6.1f}s RESULT cost total={event.data.cost.total}")
    message = await run.result()
    reply = message.content or ""

    window = store.events(after=first_seq, limit=1000, type_prefix="mcp.tool_call")
    tool_calls = [f"{e.metadata.get('tool')}:{e.metadata.get('document_id') or e.metadata.get('policy_id') or ''}"
                  for e in window]
    retrieval = retrieval_check(window, fixtures.POLICY_ID, store.document_ids())
    store.record_event("agent.run_finished", actor=actor,
                       status="ok" if message.status.value == "success" else "error",
                       metadata={**provenance, "status": message.status.value, "cost": cost,
                                 "duration_s": round(time.monotonic() - t0, 1),
                                 "mcp_tool_calls_during_run": tool_calls,
                                 "retrieval": retrieval,
                                 "reply_length": len(reply)},
                       error=message.error_code)
    summary = workflow.ingest_agent_reply(store, reply, actor=actor, provenance=provenance)

    out("-" * 30 + " agent reply " + "-" * 30)
    out(reply)
    out("-" * 73)
    out(f"MCP tool calls recorded server-side during the run: {tool_calls or 'none'}")
    out(f"retrieval check: complete={retrieval['complete']} policy v{retrieval['policy_versions']} "
        f"documents {retrieval['document_versions']} missing={retrieval['missing']}")
    out(f"classifications: {summary['classifications']}")
    out(f"proposals (pending_review): {summary['proposals'] or 'none'}")
    for err in summary["errors"]:
        out(f"error: {err}")
    graded = {k: summary["classifications"].get(k) == v
              for k, v in fixtures.EXPECTED_CLASSIFICATION.items()}
    out(f"grading vs fixtures: {graded}")

    record.update(provenance=provenance, cost=cost, status=message.status.value,
                  reply=config.redact(reply), summary=summary, mcp_tool_calls=tool_calls,
                  retrieval=retrieval,
                  grading=graded, finished_at=datetime.now(UTC).isoformat())
    path = ROOT / "runs" / f"impact_{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(config.redact(json.dumps(record, indent=2, default=str)), encoding="utf-8")
    out(f"Evidence saved to {path.relative_to(ROOT)}")
    ok = (message.status.value == "success" and retrieval["complete"] and summary["proposals"]
          and all(graded.values()))
    return 0 if ok else 1


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true", help="start ONE paid agent run")
    parser.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS)
    args = parser.parse_args()

    config.load_env()
    env = require_env("COMINTY_API_KEY", "COMINTY_USER_ID", "COMINTY_AGENT_ID")
    connection = os.environ.get("UNDRIFT_MCP_CONNECTION", "undrift").strip()
    store = Store()
    store.init()
    try:
        async with AsyncCominty() as client:
            ready, _ = await free_checks(client, env["COMINTY_AGENT_ID"], connection)
            ready = await instructions_match(client, env["COMINTY_AGENT_ID"]) and ready
            ready = local_checks(store) and ready
            if not args.run:
                out("\nFree checks done. No agent run was started (no credit spent).")
                return 0 if ready else 1
            if not ready:
                out("\nRefusing to start a paid run: checks failed.")
                return 1
            out(f"\nStarting ONE paid run (max_steps={args.max_steps}).\n")
            return await paid_run(client, store, env["COMINTY_AGENT_ID"], connection,
                                  args.max_steps)
    except ComintyError as exc:
        out(f"Cominty error: {type(exc).__name__}: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
