from __future__ import annotations

import asyncio
from pathlib import Path

from mcp import Client

import fixtures
import run_impact
import server
import workflow
from store import Store

DATA = Path(__file__).parent / "data"


def tool_events(store: Store):
    return store.events(type_prefix="mcp.tool_call", limit=1000)


def fetch(calls: list[tuple[str, dict]]) -> None:
    async def run() -> None:
        async with Client(server.mcp) as client:
            for name, args in calls:
                await client.call_tool(name, args)
    asyncio.run(run())


def test_prompt_allows_subagent_retrieval_and_asks_for_unmodified_results() -> None:
    prompt = run_impact.build_prompt("shiftly-returns", ["doc-001", "doc-002"])
    assert "yourself" not in prompt
    assert "subagent" in prompt and "unmodified" in prompt and "version" in prompt
    assert "doc-001, doc-002" in prompt


def test_instructions_permit_subagent_and_keep_output_contract() -> None:
    text = run_impact.INSTRUCTIONS.read_text(encoding="utf-8")
    assert "Do not delegate" not in text
    assert "integration subagent" in text and "complete and unmodified" in text
    assert "permission" in text
    for key in ('"document_version"', '"evidence"', '"reason"', '"proposed_content"',
                '"changes_made": false', "human reviews every correction"):
        assert key.lower() in text.lower()


def test_retrieval_check_counts_only_successful_server_side_calls(changed: Store) -> None:
    fetch([("get_policy", {"policy_id": fixtures.POLICY_ID}),
           ("get_document", {"document_id": "doc-001"}),
           ("get_document", {"document_id": "doc-002"}),
           ("get_document", {"document_id": "doc-999"})])
    result = run_impact.retrieval_check(tool_events(changed), fixtures.POLICY_ID, changed.document_ids())
    assert result["policy_versions"] == [2]
    assert result["document_versions"] == {"doc-001": [3], "doc-002": [1], "doc-003": [], "doc-004": []}
    assert result["missing"] == ["doc-003", "doc-004"] and result["complete"] is False

    fetch([("get_document", {"document_id": d}) for d in ("doc-003", "doc-004")])
    assert run_impact.retrieval_check(tool_events(changed), fixtures.POLICY_ID,
                                      changed.document_ids())["complete"] is True


def test_retrieval_check_without_any_calls(changed: Store) -> None:
    result = run_impact.retrieval_check([], fixtures.POLICY_ID, ["doc-001"])
    assert result["missing"] == [f"policy:{fixtures.POLICY_ID}", "doc-001"]


def test_recorded_no_delegation_reply_creates_nothing(changed: Store) -> None:
    reply = (DATA / "agent_reply_20261105_no_delegation.md").read_text("utf-8")
    summary = workflow.ingest_agent_reply(changed, reply, actor="agent:cominty:recorded",
                                          provenance={"source": "recorded-2026-11-05"})
    assert summary["proposals"] == [] and summary["classifications"] == {}
    assert summary["errors"] == [{"error": "policy version mismatch"}]
    verdict = changed.events(type_prefix="agent.verdict_received")[-1]
    assert verdict.status == "error" and verdict.metadata["reported_policy_version"] is None
    assert changed.proposals() == []
