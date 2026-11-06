from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import GOOD_FAQ, analysis_run
from starlette.testclient import TestClient
from test_run_impact import fetch
from test_workflow import edit_document_externally, reply_with

import api
import fixtures
import readmodel
import workflow
from store import Store

PROV = {"source": "cominty", "agent_id": "agent-x", "thread_id": "thread-1", "message_id": "msg-1"}
VERDICT = {
    "doc-001": {"category": "OUTDATED", "document_version": 3,
                "evidence": "All customers can return items within 60 days.",
                "reason": "60 days contradicts voluntary_return_days=30.",
                "proposed_content": GOOD_FAQ},
    "doc-002": {"category": "VALID_DISTINCT", "document_version": 1,
                "evidence": "Customers have a statutory 14-day withdrawal right."},
    "doc-003": {"category": "EXISTING_COMMITMENT", "document_version": 1,
                "evidence": fixtures.DOCUMENTS["doc-003"].content},
}


def simulate_run(store: Store, runs_dir: Path, finish: bool = True) -> str:
    actor = "agent:cominty:agent-x"
    run, secret = analysis_run(store, ["doc-001", "doc-002", "doc-003"], retrieve=False)
    fetch([("get_policy", {"policy_id": fixtures.POLICY_ID, "run_id": run.id, "run_token": secret})]
          + [("get_document", {"document_id": d, "run_id": run.id, "run_token": secret})
             for d in run.document_ids])
    if not finish:
        return run.id
    reply = "**doc-001**: outdated.\n" + reply_with(VERDICT)
    runs_dir.mkdir(exist_ok=True)
    (runs_dir / "impact_20261105T150000Z.json").write_text(json.dumps({
        "provenance": PROV, "reply": reply, "events": [
            {"name": "tool_call", "status": "success",
             "data": {"name": "subagent", "description": "Retrieve policy and documents"}},
            {"name": "llm", "status": "running", "data": {"model": "M", "description": "x"}}]}))
    workflow.ingest_agent_reply(store, reply, actor=actor, provenance=PROV, run_id=run.id,
        execution_status="success", metadata={"duration_s": 12.5, "cost": {"total": "0.05"}})
    return run.id


@pytest.fixture
def runs_dir(tmp_path: Path) -> Path:
    return tmp_path / "runs"


def test_runs_use_persisted_state_and_optional_run_records(changed: Store, runs_dir: Path) -> None:
    rid = simulate_run(changed, runs_dir)
    [run] = readmodel.agent_runs(changed, runs_dir)
    assert (run["id"], run["status"], run["thread_id"], run["duration_s"]) == (
        rid, "completed", "thread-1", 12.5)
    assert [(c["tool"], c["version"]) for c in run["mcp_calls"]] == [
        ("get_policy", 2), ("get_document", 3), ("get_document", 1), ("get_document", 1)]
    assert run["classifications"] == {"doc-001": "OUTDATED", "doc-002": "VALID_DISTINCT",
                                      "doc-003": "EXISTING_COMMITMENT"}
    assert len(run["proposals"]) == 1 and run["record_file"] == "impact_20261105T150000Z.json"
    assert run["explanation"] == "**doc-001**: outdated.\nAnalysis..."
    assert run["trace"] == [{"kind": "tool_call", "status": "success", "tool": "subagent",
                             "model": None, "description": "Retrieve policy and documents"}]


def test_run_in_progress_is_reported_as_running(changed: Store, runs_dir: Path) -> None:
    simulate_run(changed, runs_dir, finish=False)
    [run] = readmodel.agent_runs(changed, runs_dir)
    assert run["status"] == "open" and run["finished_at"] is None
    assert len(run["mcp_calls"]) == 4 and run["verdict"] is None


def test_impact_snapshot_joins_findings_evidence_and_proposals(changed: Store, runs_dir: Path) -> None:
    rid = simulate_run(changed, runs_dir)
    snap = readmodel.impact_snapshot(changed, fixtures.POLICY_ID, runs_dir)
    assert snap["policy"]["version"] == 2 and len(snap["policy_versions"]) == 2
    docs = {d["id"]: d for d in snap["documents"]}
    f1 = docs["doc-001"]["finding"]
    assert (f1["category"], f1["document_version"], f1["policy_version"], f1["current"]) == (
        "OUTDATED", 3, 2, True)
    assert f1["evidence_verified"] is True and f1["reason"].startswith("60 days")
    assert (f1["prefilter"], f1["agrees_with_prefilter"]) == ("actionable", True)
    assert [p["status"] for p in docs["doc-001"]["proposals"]] == ["pending_review"]
    assert docs["doc-003"]["finding"]["evidence_verified"] is True
    assert docs["doc-003"]["prefilter"]["protected"] == {"0": "existing_commitment"}
    assert docs["doc-004"]["finding"] is None
    assert snap["latest_run"]["id"] == rid and "verdict" not in snap["latest_run"]


def test_findings_become_stale_when_document_or_policy_moves(changed: Store, runs_dir: Path,
                                                             token: str) -> None:
    rid = simulate_run(changed, runs_dir)
    edit_document_externally(changed, "doc-002", "Customers have a statutory 14-day withdrawal right!")
    docs = {d["id"]: d for d in readmodel.impact_snapshot(changed, fixtures.POLICY_ID, runs_dir)["documents"]}
    assert docs["doc-002"]["finding"]["current"] is False
    assert docs["doc-002"]["finding"]["evidence_verified"] is True
    assert docs["doc-001"]["finding"]["current"] is True
    workflow.change_policy(changed, fixtures.POLICY_ID, token=token, voluntary_return_days=21,
                           effective_from=fixtures.SHIFTLY_RETURNS_CHANGE["effective_from"],
                           description="21 days.")
    docs = {d["id"]: d for d in readmodel.impact_snapshot(changed, fixtures.POLICY_ID, runs_dir)["documents"]}
    assert docs["doc-001"]["finding"]["current"] is False


def test_missing_run_record_keeps_persisted_evidence(changed: Store, runs_dir: Path) -> None:
    rid = simulate_run(changed, runs_dir)
    for f in runs_dir.iterdir():
        f.unlink()
    finding = {d["id"]: d for d in readmodel.impact_snapshot(
        changed, fixtures.POLICY_ID, runs_dir)["documents"]}["doc-001"]["finding"]
    assert finding["category"] == "OUTDATED" and finding["evidence"] == fixtures.DOCUMENTS["doc-001"].content
    assert finding["document_version"] == 3 and finding["evidence_verified"] is True


def test_malformed_optional_trace_cannot_break_persisted_product_state(changed: Store, runs_dir: Path) -> None:
    rid = simulate_run(changed, runs_dir)
    path = next(runs_dir.glob('impact_*.json'))
    path.write_text(json.dumps({'provenance': PROV, 'reply': None,
        'events': [None, {'status': [], 'data': []}, {'status': 'success', 'data': 'bad'}]}))
    readmodel._record_cache.clear()
    snap = readmodel.impact_snapshot(changed, fixtures.POLICY_ID, runs_dir)
    assert snap['latest_run']['id'] == rid and snap['latest_run']['status'] == 'completed'
    assert snap['documents'][0]['finding']['evidence_verified']
    assert readmodel.agent_runs(changed, runs_dir)[0]['trace'] == []


def client_for(store: Store, runs_dir: Path, ui_dir: Path | None = None) -> TestClient:
    return TestClient(api.build_app(store, runs_dir=runs_dir, ui_dir=ui_dir),
                      base_url="http://127.0.0.1:8100", client=("127.0.0.1", 1))


def test_impact_runs_and_proposal_check_endpoints(changed: Store, runs_dir: Path) -> None:
    rid = simulate_run(changed, runs_dir)
    c = client_for(changed, runs_dir)
    snap = c.get(f"/api/policies/{fixtures.POLICY_ID}/impact").json()
    assert len(snap["documents"]) == 4
    assert c.get("/api/policies/nope/impact").status_code == 404
    runs = c.get("/api/runs").json()["runs"]
    assert [r["id"] for r in runs] == [rid] and "verdict" not in runs[0]
    assert c.get(f"/api/runs/{rid}").json()["run"]["verdict"]["documents"]["doc-001"]
    assert c.get("/api/runs/nope").status_code == 404
    pid = runs[0]["proposals"][0]
    detail = c.get(f"/api/proposals/{pid}").json()
    assert detail["checks"] == {"document_current_version": 3, "policy_current_version": 2,
                                "document_unchanged": True, "policy_current": True}
    assert detail["review_flags"] == []


def test_events_tail_returns_newest_in_ascending_order(changed: Store, runs_dir: Path) -> None:
    c = client_for(changed, runs_dir)
    body = c.get("/api/events", params={"tail": 2}).json()
    seqs = [e["seq"] for e in body["events"]]
    assert len(seqs) == 2 and seqs == sorted(seqs) and seqs[-1] == body["latest_seq"]


def test_session_endpoint_verifies_token_without_side_effects(changed: Store, runs_dir: Path,
                                                              token: str) -> None:
    c = client_for(changed, runs_dir)
    assert c.post("/api/session").status_code == 401
    assert c.post("/api/session", headers={"authorization": "Bearer nope"}).status_code == 401
    r = c.post("/api/session", headers={"authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.json() == {"reviewer": "test-reviewer"}
    assert changed.proposals() == [] and len(changed.events(type_prefix="auth.denied")) == 2


def test_built_ui_is_served_with_security_headers(changed: Store, runs_dir: Path,
                                                  tmp_path: Path) -> None:
    assert client_for(changed, runs_dir).get("/").status_code == 404
    ui = tmp_path / "dist"
    ui.mkdir()
    (ui / "index.html").write_text("<!doctype html><title>Undrift</title>")
    c = client_for(changed, runs_dir, ui)
    r = c.get("/")
    assert r.status_code == 200 and "Undrift" in r.text
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert c.get("/api/health").json()["status"] == "ok"
    assert c.get("/", headers={"host": "evil.example"}).status_code == 403
