from __future__ import annotations

import asyncio
from pathlib import Path

from mcp import Client

import fixtures
import run_impact
import server
import workflow
from conftest import analysis_run
from store import Store

DATA = Path(__file__).parent / "data"


def fetch(calls: list[tuple[str, dict]]) -> None:
    async def run() -> None:
        async with Client(server.mcp) as client:
            for name, args in calls:
                await client.call_tool(name, args)
    asyncio.run(run())


def test_prompt_allows_subagent_retrieval_and_asks_for_unmodified_results() -> None:
    prompt = run_impact.build_prompt("shiftly-returns", ["doc-001", "doc-002"],
                                     "run-0123456789abcdef", "urt_" + "t" * 43)
    assert "yourself" not in prompt
    assert "subagent" in prompt and "unmodified" in prompt and "version" in prompt
    assert "doc-001, doc-002" in prompt
    assert "record_finding" in prompt and "EVERY get_policy" in prompt
    assert "run-0123456789abcdef" in prompt and "urt_" + "t" * 43 in prompt


def test_instructions_permit_subagent_and_keep_output_contract() -> None:
    text = run_impact.INSTRUCTIONS.read_text(encoding="utf-8")
    assert "Do not delegate" not in text
    assert "integration subagent" in text and "complete and unmodified" in text
    assert "permission" in text
    for key in ('"document_version"', '"evidence"', '"reason"', '"proposed_content"',
                '"changes_made": false', "human reviews every correction"):
        assert key.lower() in text.lower()


def test_retrieval_check_counts_only_authenticated_exact_versions(changed: Store) -> None:
    run, secret = analysis_run(changed, retrieve=False)
    fetch([("get_document", {"document_id": "doc-001"})])
    assert workflow.retrieval_status(changed, run)["document_versions"]["doc-001"] == []
    credentials = {"run_id": run.id, "run_token": secret}
    fetch([("get_policy", {"policy_id": fixtures.POLICY_ID, **credentials})]
          + [("get_document", {"document_id": did, **credentials}) for did in ("doc-001", "doc-002")])
    result = workflow.retrieval_status(changed, run)
    assert result["policy_versions"] == [2]
    assert result["document_versions"] == {"doc-001": [3], "doc-002": [1], "doc-003": [], "doc-004": []}
    assert result["missing"] == ["doc-003", "doc-004"] and not result["complete"]
    fetch([("get_document", {"document_id": did, **credentials}) for did in ("doc-003", "doc-004")])
    assert workflow.retrieval_status(changed, run)["complete"]


def test_retrieval_check_without_any_calls(changed: Store) -> None:
    run, _ = analysis_run(changed, ["doc-001"], retrieve=False)
    assert workflow.retrieval_status(changed, run)["missing"] == [f"policy:{fixtures.POLICY_ID}", "doc-001"]


def test_recorded_no_delegation_reply_creates_nothing(changed: Store) -> None:
    reply = (DATA / "agent_reply_20261105_no_delegation.md").read_text("utf-8")
    run, _ = analysis_run(changed)
    summary = workflow.ingest_agent_reply(changed, reply, run_id=run.id, execution_status="success", actor="agent:cominty:recorded",
                                          provenance={"source": "recorded-2026-11-05"})
    assert summary["proposals"] == [] and summary["classifications"] == {}
    assert summary["errors"] == [{"error": "finding reason must be 1-1000 characters",
                                   "code": "invalid_request", "document_id": "doc-001"}]
    verdict = changed.events(type_prefix="agent.reply_rejected")[-1]
    assert verdict.status == "error"
    assert changed.proposals() == []
