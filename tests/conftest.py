from __future__ import annotations

import secrets
from collections.abc import Iterator

import pytest

import fixtures
import server
import workflow
from store import Store

GOOD_FAQ = (
    "Items purchased on or after November 4, 2026 can be returned within 30 days. "
    "Purchases made before November 4, 2026 keep the 60-day return period promised at the "
    "time of purchase."
)


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("MCP_PATH_SECRET", "MCP_PATH_SECRET_NEXT", "COMINTY_API_KEY",
                 "UNDRIFT_REVIEW_TOKEN", "UNDRIFT_REVIEWER", "UNDRIFT_MCP_WRITE_TOOLS",
                 "UNDRIFT_MCP_FINDINGS", "UNDRIFT_MCP_FINDINGS_PUBLIC",
                 "MCP_PUBLIC_HOST", "UNDRIFT_DB"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def token(monkeypatch: pytest.MonkeyPatch) -> str:
    value = secrets.token_urlsafe(32)
    monkeypatch.setenv("UNDRIFT_REVIEW_TOKEN", value)
    monkeypatch.setenv("UNDRIFT_REVIEWER", "test-reviewer")
    return value


@pytest.fixture
def store(tmp_path) -> Iterator[Store]:
    s = Store(tmp_path / "undrift.db")
    s.init()
    previous = server._state["store"]
    server.use_store(s)
    yield s
    server.use_store(previous)


@pytest.fixture
def changed(store: Store, token: str) -> Store:
    workflow.change_policy(store, fixtures.POLICY_ID, token=token, **fixtures.SHIFTLY_RETURNS_CHANGE)
    return store


def propose(store: Store, content: str = GOOD_FAQ, document_id: str = "doc-001",
            actor: str = "agent:test", **overrides):
    doc = store.document(document_id)
    policy = store.policy(fixtures.POLICY_ID)
    kwargs = dict(document_id=document_id, base_version=doc.version, policy_id=policy.id,
                  policy_version=policy.version, proposed_content=content,
                  reason="States 60 days; policy v2 sets 30 days from 2026-11-04.", actor=actor)
    return workflow.submit_proposal(store, **{**kwargs, **overrides})


def analysis_run(store: Store, ids: list[str] | None = None, retrieve: bool = True):
    run, secret = workflow.open_run(store, fixtures.POLICY_ID, ids or store.document_ids(),
                                    actor='agent:test', agent_id='agent-x')
    if retrieve:
        workflow.retrieve_for_run(store, run_id=run.id, run_token=secret, kind='policy',
                                  subject_id=fixtures.POLICY_ID, actor='mcp:test')
        for did in run.document_ids:
            workflow.retrieve_for_run(store, run_id=run.id, run_token=secret, kind='document',
                                      subject_id=did, actor='mcp:test')
    return run, secret


@pytest.fixture
def findings_tool():
    server.enable_findings_tool(None)
    yield
    server.mcp.remove_tool('record_finding')
    server._state['findings_tool'] = False
