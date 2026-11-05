"""Cominty Managed Agent -> Undrift MCP integration spike.

    python spike.py          # free checks only: credentials, agent, MCP integration
    python spike.py --run    # ONE paid agent run (spends Cominty credit)

Reads COMINTY_API_KEY, COMINTY_USER_ID, COMINTY_AGENT_ID (and optionally
UNDRIFT_MCP_CONNECTION, default "undrift") from the environment or .env.
Every printed or saved string is scrubbed of MCP_PATH_SECRET.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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
from fixtures import DOCUMENTS, EXPECTED_CLASSIFICATION

ROOT = Path(__file__).resolve().parent
POLICY_ID = "shiftly-returns"
DEFAULT_MAX_STEPS = 6  # 1 policy + 4 documents need 5 tool calls; the SDK allows up to max_steps+1

PROMPT = (
    f"Use the Undrift MCP tools. Retrieve policy '{POLICY_ID}' with get_policy, then retrieve "
    f"each of these documents with get_document: {', '.join(sorted(DOCUMENTS))}. "
    "Classify every document against the current policy and justify each classification with "
    "an exact quote from the retrieved data. This is read-only analysis: change nothing. "
    "End with the JSON block described in your instructions."
)


def out(text: str) -> None:
    print(config.redact(text))


def require_env(*names: str) -> dict[str, str]:
    missing = [n for n in names if not os.environ.get(n, "").strip()]
    if missing:
        raise SystemExit(f"Missing environment variables: {', '.join(missing)} (see SETUP.md)")
    return {n: os.environ[n].strip() for n in names}


async def free_checks(client: AsyncCominty, agent_id: str, connection: str) -> tuple[bool, list[str]]:
    ok = True
    agent = await client.agents.get(agent_id)
    models = [m.name for m in agent.models]
    mcp_cap = agent.capabilities.mcp if agent.capabilities else None
    out(f"agent        {agent.name!r} id={agent.id} scope={agent.scope}")
    if agent.models:
        out(f"primary      {agent.models[0].name} [{agent.models[0].id}]")
        out(f"failover     {', '.join(models[1:]) or '(none)'}")
    else:
        out("primary      platform default")
    expected = os.environ.get("UNDRIFT_EXPECTED_MODEL", "").strip()
    if expected:
        match_chain = models == [expected]
        out(f"model gate   expected chain [{expected!r}]: {'OK' if match_chain else 'MISMATCH'}")
        ok = ok and match_chain
    out(f"agent mcp    activation={mcp_cap.activation if mcp_cap else None} "
        f"connections={mcp_cap.connections if mcp_cap else None}")
    if not (mcp_cap and mcp_cap.activation != "never" and mcp_cap.connections == [connection]):
        out(f"agent mcp    MISMATCH: expected exactly [{connection!r}] activated")
        ok = False

    servers = await client.mcp.list(enabled=None)
    match = next((s for s in servers if s.name == connection), None)
    if match is None:
        out(f"mcp          NOT FOUND: no integration named {connection!r}. "
            f"Visible: {[s.name for s in servers]}")
        return False, models
    out(f"mcp          title={match.title!r} name={match.name} enabled={match.is_enabled} "
        f"origin={match.origin} scope={match.scope} oauth={match.requires_oauth_connect} "
        f"status={match.connection_status}")
    if not match.is_enabled:
        out("mcp          integration is DISABLED for this key: turn it on in the platform")
        ok = False
    # Never print mcp_url: it contains the path secret. Compare it instead.
    if match.mcp_url:
        u = urlparse(match.mcp_url)
        want_host = config.public_host()
        slots = {name: os.environ.get(env, "").strip() for name, env in
                 (("current", "MCP_PATH_SECRET"), ("next", "MCP_PATH_SECRET_NEXT"))}
        path_slot = next((name for name, s in slots.items()
                          if s and u.path.rstrip("/") == config.mcp_path(s)), "none")
        want_slot = "next" if slots["next"] else "current"
        host_ok = bool(want_host) and u.hostname == want_host
        out(f"mcp url      scheme={u.scheme} host_matches_MCP_PUBLIC_HOST={host_ok} "
            f"path_matches_secret={path_slot} (expected {want_slot})")
        ok = ok and u.scheme == "https" and host_ok and path_slot == want_slot
    else:
        out("mcp url      not returned by the API: cannot verify host and path")
        ok = False
    return ok, models


def parse_verdict(reply: str) -> dict[str, Any] | None:
    blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", reply, flags=re.DOTALL)
    for block in reversed(blocks):
        try:
            return json.loads(block)
        except json.JSONDecodeError:
            continue
    return None


def model_values(obj: Any) -> set[str]:
    """Every string under a key containing 'model' anywhere in a raw event log."""
    found: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if "model" in str(k).lower() and isinstance(v, str):
                found.add(v)
            found |= model_values(v)
    elif isinstance(obj, list):
        for v in obj:
            found |= model_values(v)
    return found


async def paid_run(client: AsyncCominty, agent_id: str, connection: str, max_steps: int,
                   configured_models: list[str]) -> int:
    record: dict[str, Any] = {"started_at": datetime.now(UTC).isoformat(), "agent_id": agent_id,
                              "max_steps": max_steps, "prompt": PROMPT,
                              "configured_models": configured_models, "events": []}
    tool_calls: list[dict[str, Any]] = []
    stream_models: list[str] = []
    cost: Decimal | None = None
    t0 = time.monotonic()

    run = await client.chat.start(
        agent_id=agent_id,
        message=PROMPT,
        name="undrift-preflight",
        max_steps=max_steps,
        # Frozen for this thread: only our MCP integration; no web, docs, skills, memory.
        thread_capabilities=AgentCapabilities(
            mcp=McpCapability(activation="always", connections=[connection]),
            web=WebCapability(activation="never"),
            indexed_documents=IndexedDocumentsCapability(activation="never"),
            skills=SkillsCapability(activation="never"),
            image_generation=ImageGenerationCapability(activation="never"),
            memory=MemoryCapability(activation="never"),
        ),
    )
    out(f"thread={run.thread.id} message={run.message_id}")
    record.update(thread_id=str(run.thread.id), message_id=str(run.message_id))

    async with run:
        async for event in run:
            dt = time.monotonic() - t0
            record["events"].append(json.loads(config.redact(event.model_dump_json())))
            if isinstance(event, events.ToolCall):
                line = f"{dt:6.1f}s TOOL {event.status:<7} {event.data.name}  {event.data.description[:70]}"
                if event.status == "error":
                    line += f"  ERROR: {event.data.error}"
                out(line)
                if event.status != "running":
                    tool_calls.append({"name": event.data.name, "status": event.status,
                                       "error": event.data.error})
            elif isinstance(event, events.LlmStep):
                stream_models.append(event.data.model)
                out(f"{dt:6.1f}s LLM  {event.status:<7} model={event.data.model!r}  "
                    f"{event.data.description[:60]}")
            elif isinstance(event, events.Result):
                c = event.data.cost
                cost = c.total
                out(f"{dt:6.1f}s RESULT cost total={c.total} input={c.input_cost} output={c.output_cost} "
                    f"tokens in/cached/out={c.input_tokens}/{c.cached_tokens}/{c.output_tokens} "
                    f"failed={c.failed}")
                record["cost"] = json.loads(c.model_dump_json())
            elif isinstance(event, events.IntermediaryUpdate):
                out(f"{dt:6.1f}s NOTE {event.data.message[:90]}")
            else:
                out(f"{dt:6.1f}s {event.name} {event.status}")

    message = await run.result()
    reply = message.content or ""
    raw_models = sorted(model_values(message.events))
    observed = sorted(set(stream_models) | set(raw_models))
    record.update(status=message.status.value, error_code=message.error_code,
                  reply=config.redact(reply), tool_calls=tool_calls,
                  message_agent=str(message.agent), stream_models=sorted(set(stream_models)),
                  raw_event_models=raw_models,
                  raw_message_events=json.loads(config.redact(json.dumps(message.events, default=str))))
    out(f"\nstatus={message.status.value} error_code={message.error_code}")
    out(f"models       configured={configured_models} stream={sorted(set(stream_models))} "
        f"raw_events={raw_models}\n")
    out("-" * 30 + " agent reply " + "-" * 30)
    out(reply)
    out("-" * 73)

    # ---- grading: only facts observed in this run ----
    checks: list[tuple[str, bool, str]] = []
    ok_calls = [t for t in tool_calls if t["status"] == "success"]
    pol = [t for t in ok_calls if "get_policy" in t["name"]]
    doc = [t for t in ok_calls if "get_document" in t["name"]]
    checks.append(("message status success", message.status.value == "success", message.status.value))
    checks.append(("execution model reported", bool(observed), f"observed={observed}"))
    checks.append(("observed model == configured model name",
                   bool(observed) and set(observed) <= set(configured_models),
                   f"observed={observed} configured={configured_models} "
                   "(display strings; review manually if they differ)"))
    checks.append(("get_policy invoked successfully", bool(pol), f"{len(pol)} call(s)"))
    checks.append(("get_document invoked for all 4 docs", len(doc) >= 4, f"{len(doc)} call(s)"))
    errs = [t for t in tool_calls if t["status"] == "error"]
    checks.append(("no tool errors", not errs, f"{len(errs)} error(s)"))

    verdict = parse_verdict(reply)
    checks.append(("final JSON block parsed", verdict is not None, ""))
    if verdict:
        got = {k: (v or {}).get("category") for k, v in verdict.get("documents", {}).items()}
        for doc_id, want in EXPECTED_CLASSIFICATION.items():
            checks.append((f"{doc_id} classified {want}", got.get(doc_id) == want, f"got {got.get(doc_id)}"))
        p = verdict.get("policy", {})
        checks.append(("policy 30 days reported", p.get("voluntary_return_days") == 30,
                       f"got {p.get('voluntary_return_days')}"))
        checks.append(("no change claimed", verdict.get("changes_made") is False,
                       f"changes_made={verdict.get('changes_made')}"))
    checks.append(("cost reported", cost is not None, f"total={cost}"))

    out("")
    for name, passed, ev in checks:
        out(f"[{'PASS' if passed else 'FAIL'}] {name} {ev}")
    record["checks"] = [{"check": n, "pass": p, "evidence": e} for n, p, e in checks]
    record["finished_at"] = datetime.now(UTC).isoformat()

    path = ROOT / "runs" / f"spike_{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(config.redact(json.dumps(record, indent=2, default=str)), encoding="utf-8")
    out(f"\nEvidence saved to {path.relative_to(ROOT)}")
    return 0 if all(p for _, p, _ in checks) else 1


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true", help="start ONE paid agent run")
    parser.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS)
    args = parser.parse_args()

    config.load_env()
    env = require_env("COMINTY_API_KEY", "COMINTY_USER_ID", "COMINTY_AGENT_ID")
    connection = os.environ.get("UNDRIFT_MCP_CONNECTION", "undrift").strip()

    try:
        async with AsyncCominty() as client:  # reads COMINTY_API_KEY + COMINTY_USER_ID
            ready, models = await free_checks(client, env["COMINTY_AGENT_ID"], connection)
            if not args.run:
                out("\nFree checks done. No agent run was started (no credit spent).")
                out("Re-run with --run to start ONE paid run." if ready else "Fix the issues above first.")
                return 0 if ready else 1
            if not ready:
                out("\nRefusing to start a paid run: free checks failed.")
                return 1
            out(f"\nStarting ONE paid run (max_steps={args.max_steps}). "
                "The SDK has no hard spending cap; cost is known only afterwards.\n")
            return await paid_run(client, env["COMINTY_AGENT_ID"], connection, args.max_steps,
                                  models)
    except ComintyError as exc:
        out(f"Cominty error: {type(exc).__name__}: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
