from __future__ import annotations

import asyncio
import json
import secrets
import threading
from datetime import datetime

import pytest
from mcp import Client

import config
import server
from store import Store


def assert_ordered(events) -> None:
    seqs = [e.seq for e in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    stamps = [e.ts for e in events]
    assert stamps == sorted(stamps)


def test_concurrent_mcp_tool_calls_are_all_logged(changed: Store) -> None:
    doc_ids = changed.document_ids()

    async def run() -> list:
        async with Client(server.mcp) as client:
            calls = [client.call_tool("get_document", {"document_id": doc_ids[i % 4]})
                     for i in range(40)]
            calls += [client.call_tool("get_policy", {"policy_id": "shiftly-returns"})
                      for _ in range(10)]
            calls.append(client.call_tool("get_document", {"document_id": "doc-999"}))
            return await asyncio.gather(*calls)

    results = asyncio.run(run())
    assert sum(r.is_error for r in results) == 1
    events = changed.events(type_prefix="mcp.tool_call", limit=1000)
    assert len(events) == 51
    assert_ordered(events)
    by_tool = {t: [e for e in events if e.metadata["tool"] == t] for t in ("get_document", "get_policy")}
    assert len(by_tool["get_document"]) == 41 and len(by_tool["get_policy"]) == 10
    assert {e.metadata["version"] for e in by_tool["get_policy"]} == {2}
    failed = [e for e in events if e.status == "error"]
    assert len(failed) == 1 and "doc-999" in failed[0].error
    assert all(e.subject_type == "document" for e in by_tool["get_document"])


def test_mcp_events_never_contain_document_content(changed: Store) -> None:
    async def run() -> None:
        async with Client(server.mcp) as client:
            for doc_id in changed.document_ids():
                await client.call_tool("get_document", {"document_id": doc_id})

    asyncio.run(run())
    raw = json.dumps([e.model_dump(mode="json") for e in changed.events(limit=1000)])
    for doc in changed.documents():
        assert doc.content not in raw
    assert "60 days" not in raw


def test_threaded_writers_keep_a_total_order(store: Store) -> None:
    errors: list[Exception] = []

    def writer(n: int) -> None:
        try:
            for i in range(25):
                store.record_event("test.write", actor=f"thread-{n}", metadata={"i": i})
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    events = store.events(type_prefix="test.", limit=1000)
    assert len(events) == 200
    assert_ordered(events)
    for n in range(8):
        mine = [e.metadata["i"] for e in events if e.actor == f"thread-{n}"]
        assert mine == list(range(25))


def test_event_paging_and_filters(store: Store) -> None:
    for i in range(5):
        store.record_event("test.page", actor="t", subject=("document", f"doc-{i}"))
    first = store.events(type_prefix="test.", limit=2)
    rest = store.events(type_prefix="test.", after=first[-1].seq)
    assert [e.subject_id for e in first + rest] == [f"doc-{i}" for i in range(5)]
    assert [e.subject_id for e in store.events(subject=("document", "doc-3"))] == ["doc-3"]
    assert store.events(type_prefix="test%") == []
    assert isinstance(first[0].ts, datetime) and first[0].ts.tzinfo is not None


def test_invalid_event_type_is_refused(store: Store) -> None:
    with pytest.raises(ValueError):
        store.record_event("Not A Type", actor="t")


def test_secret_redaction(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    path_secret, api_key = secrets.token_urlsafe(32), "ck-" + secrets.token_urlsafe(24)
    previous_secret = secrets.token_urlsafe(32)
    monkeypatch.setenv("MCP_PATH_SECRET", path_secret)
    monkeypatch.setenv("COMINTY_API_KEY", api_key)
    store.record_event("test.redaction", actor=f"mcp:{api_key}", status="error", metadata={
        "url": f"https://x.trycloudflare.com/mcp/{path_secret}",
        "old_url": f"https://x.trycloudflare.com/mcp/{previous_secret}",
        "api_key": "anything", "review_token": "anything", "Authorization": "Bearer abc",
        "nested": {"headers": [f"authorization: Bearer {secrets.token_urlsafe(16)}"]},
        "content": "Customer Jane Doe, IBAN DE00 1234",
        "input_tokens": 1234, "note": "x" * 50, "long": "y" * 1000,
    }, error=f"failed at /mcp/{path_secret}")
    e = store.events(type_prefix="test.redaction")[0]
    raw = json.dumps(e.model_dump(mode="json")) + store.path.read_bytes().decode("latin-1")
    for leaked in (path_secret, api_key, previous_secret, "Jane Doe"):
        assert leaked not in raw
    m = e.metadata
    assert m["url"] == "https://x.trycloudflare.com/mcp/<redacted>"
    assert m["old_url"] == "https://x.trycloudflare.com/mcp/<redacted>"
    assert m["api_key"] == m["review_token"] == m["Authorization"] == "<redacted>"
    assert "Bearer <redacted>" in m["nested"]["headers"][0]
    assert m["content"]["omitted"] == "content" and m["content"]["length"] == 33
    assert m["input_tokens"] == 1234
    assert m["note"]["omitted"] == "content"
    assert m["long"].endswith("<truncated>") and len(m["long"]) < 400
    assert e.actor == "mcp:<redacted>"
    assert e.error == "failed at /mcp/<redacted>"


def test_redact_keeps_explicit_secret_behaviour() -> None:
    secret = "s" * 40
    assert config.redact(f"/mcp/{secret}", secret) == "/mcp/<redacted>"
    assert config.redact("no secrets here") == "no secrets here"
