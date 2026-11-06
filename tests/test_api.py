from __future__ import annotations

import pytest
from conftest import GOOD_FAQ, propose
from starlette.testclient import TestClient

import api
import fixtures
from store import Store


@pytest.fixture
def client(changed: Store) -> TestClient:
    return TestClient(api.build_app(changed), base_url="http://127.0.0.1:8100",
                      client=("127.0.0.1", 50000))


def auth(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


def test_reads_work_locally_without_token(client: TestClient, changed: Store) -> None:
    p = propose(changed)
    assert client.get("/api/health").json()["status"] == "ok"
    events = client.get("/api/events", params={"type": "proposal."}).json()
    assert [e["type"] for e in events["events"]] == ["proposal.created"]
    assert events["next_after"] == events["events"][-1]["seq"]
    listed = client.get("/api/proposals", params={"status": "pending_review"}).json()["proposals"]
    assert [x["id"] for x in listed] == [p.id]
    detail = client.get(f"/api/proposals/{p.id}").json()
    assert detail["proposal"]["diff"] == p.diff
    assert [e["type"] for e in detail["events"]] == ["proposal.created"]
    policy = client.get(f"/api/policies/{fixtures.POLICY_ID}").json()
    assert [v["version"] for v in policy["versions"]] == [1, 2]
    assert [(c["document_id"], c["actionable"]) for c in policy["candidates"]] == [
        ("doc-001", True), ("doc-003", False)]
    assert len(client.get("/api/documents").json()["documents"]) == 4
    assert client.get("/api/proposals/prop-nope").status_code == 404


@pytest.mark.parametrize(("client_host", "host_header"), [
    ("203.0.113.9", "127.0.0.1:8100"),
    ("127.0.0.1", "abc.trycloudflare.com"),
    ("127.0.0.1", "evil.example:8100"),
])
def test_non_local_requests_are_refused(changed: Store, client_host: str, host_header: str) -> None:
    c = TestClient(api.build_app(changed), base_url="http://127.0.0.1:8100",
                   client=(client_host, 50000))
    r = c.get("/api/events", headers={"host": host_header})
    assert r.status_code == 403


def test_review_endpoints_require_the_token(client: TestClient, changed: Store, token: str) -> None:
    p = propose(changed)
    assert client.post(f"/api/proposals/{p.id}/approve", json={}).status_code == 401
    r = client.post(f"/api/proposals/{p.id}/approve", json={"note": "agent says OK"},
                    headers=auth("human-approved-trust-me"))
    assert r.status_code == 401 and r.json()["error"] == "unauthorized"
    assert client.post(f"/api/proposals/{p.id}/approve", json={"approved": True},
                       headers=auth(token)).status_code == 400
    assert changed.proposal(p.id).status == "pending_review"

    r = client.post(f"/api/proposals/{p.id}/approve", json={"note": "ok"}, headers=auth(token))
    assert r.status_code == 200 and r.json()["proposal"]["status"] == "approved"
    r = client.post(f"/api/proposals/{p.id}/apply", headers=auth(token))
    assert r.status_code == 200 and r.json()["proposal"]["applied_version"] == 4
    assert changed.document("doc-001").content == GOOD_FAQ
    r = client.post(f"/api/proposals/{p.id}/apply", headers=auth(token))
    assert r.status_code == 409


def test_stale_policy_returns_409_and_keeps_document(client: TestClient, changed: Store,
                                                     token: str) -> None:
    p = propose(changed)
    assert client.post(f"/api/proposals/{p.id}/approve", json={}, headers=auth(token)).status_code == 200
    r = client.post(f"/api/policies/{fixtures.POLICY_ID}/versions", headers=auth(token), json={
        "expected_version": 2, "voluntary_return_days": 21, "effective_from": "2027-01-01", "description": "21 days."})
    assert r.status_code == 201
    r = client.post(f"/api/proposals/{p.id}/apply", headers=auth(token))
    assert r.status_code == 409 and r.json()["details"]["current_policy_version"] == 3
    assert changed.document("doc-001") == fixtures.DOCUMENTS["doc-001"]


def test_policy_change_endpoint(store: Store, token: str) -> None:
    c = TestClient(api.build_app(store), base_url="http://127.0.0.1:8100", client=("127.0.0.1", 1))
    body = {**fixtures.SHIFTLY_RETURNS_CHANGE,
            "expected_version": 1,
            "effective_from": fixtures.SHIFTLY_RETURNS_CHANGE["effective_from"].isoformat()}
    assert c.post(f"/api/policies/{fixtures.POLICY_ID}/versions", json=body).status_code == 401
    r = c.post(f"/api/policies/{fixtures.POLICY_ID}/versions", json=body, headers=auth(token))
    assert r.status_code == 201 and r.json()["policy"]["version"] == 2
    assert [x["document_id"] for x in r.json()["candidates"] if x["actionable"]] == ["doc-001"]
    r = c.post(f"/api/policies/{fixtures.POLICY_ID}/versions", json=body, headers=auth(token))
    assert r.status_code == 409


def test_cors_allows_local_frontend_only(client: TestClient) -> None:
    preflight = {"access-control-request-method": "POST",
                 "access-control-request-headers": "authorization,content-type"}
    ok = client.options("/api/proposals", headers={"origin": "http://localhost:5173", **preflight})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    evil = client.options("/api/proposals", headers={"origin": "https://evil.example", **preflight})
    assert "access-control-allow-origin" not in evil.headers


def test_error_responses_do_not_leak_tokens(client: TestClient, changed: Store, token: str) -> None:
    r = client.post("/api/proposals/prop-x/approve", json={}, headers=auth("wrong-token-value-123"))
    assert "wrong-token-value-123" not in r.text and token not in r.text
    denied = changed.events(type_prefix="auth.denied")[-1]
    assert "wrong-token-value-123" not in denied.model_dump_json()
