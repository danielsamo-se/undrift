"""Read-only views for the UI: agent runs and the impact snapshot.

Everything here is derived from persisted data: the SQLite store, the activity log, and the
redacted run records that run_impact.py writes to runs/impact_*.json. The activity log never
holds document text, so the agent's evidence quotes come from those run records; each quote
is checked against the stored document version it refers to.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import config
import workflow
from models import Event
from store import Store

RUNS_DIR = config.ROOT / "runs"
RUN_RESULT_TYPES = ("agent.verdict_received", "agent.classification", "agent.reply_unparsed",
                    "agent.reply_rejected", "proposal.created", "proposal.submission_failed")

_record_cache: dict[Path, tuple[float, dict[str, Any] | None]] = {}


def _load_record(path: Path) -> dict[str, Any] | None:
    mtime = path.stat().st_mtime
    cached = _record_cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        record = record if isinstance(record, dict) else None
    except (OSError, ValueError):
        record = None
    _record_cache[path] = (mtime, record)
    return record


def run_records(runs_dir: Path = RUNS_DIR) -> dict[str, dict[str, Any]]:
    """Run records keyed by Cominty message id."""
    out: dict[str, dict[str, Any]] = {}
    if not runs_dir.is_dir():
        return out
    for path in sorted(runs_dir.glob("impact_*.json")):
        record = _load_record(path)
        message_id = ((record or {}).get("provenance") or {}).get("message_id")
        if message_id:
            out[str(message_id)] = {**record, "file": path.name}  # type: ignore[dict-item]
    return out


def _explanation(reply: str) -> str:
    return reply.split("```json", 1)[0].strip()


def _trace(stream: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Condensed Cominty stream: finished LLM steps and tool calls, as reported by Cominty."""
    steps = []
    for e in stream:
        data = e.get("data") or {}
        if e.get("status") not in {"success", "error"} or e.get("name") not in {"llm", "tool_call"}:
            continue
        steps.append({"kind": e["name"], "status": e["status"], "tool": data.get("name"),
                      "model": data.get("model"),
                      "description": config.safe_text(str(data.get("description") or ""), 400)})
    return steps


def _call(e: Event) -> dict[str, Any]:
    m = e.metadata
    return {"seq": e.seq, "ts": e.ts.isoformat(), "status": e.status, "tool": m.get("tool"),
            "document_id": m.get("document_id"), "policy_id": m.get("policy_id"),
            "version": m.get("version"), "secret_slot": m.get("secret_slot"),
            "user_agent": m.get("user_agent"), "duration_ms": m.get("duration_ms"),
            "error": e.error}


def agent_runs(store: Store, runs_dir: Path = RUNS_DIR,
               events: list[Event] | None = None) -> list[dict[str, Any]]:
    """Agent runs reconstructed from run_started/run_finished events, newest first."""
    events = events if events is not None else store.events(limit=100_000)
    records = run_records(runs_dir)
    starts = [e for e in events if e.type == "agent.run_started"]
    runs = []
    for i, start in enumerate(starts):
        next_start = starts[i + 1].seq if i + 1 < len(starts) else None
        finish = next((e for e in events if e.type == "agent.run_finished" and e.seq > start.seq
                       and (next_start is None or e.seq < next_start)), None)
        meta = finish.metadata if finish else {}
        message_id = meta.get("message_id")
        end = finish.seq if finish else (next_start or float("inf"))
        calls = [_call(e) for e in events
                 if e.type == "mcp.tool_call" and start.seq < e.seq < end]
        results = [e for e in events if finish and e.seq > finish.seq and e.type in RUN_RESULT_TYPES
                   and (e.metadata.get("provenance") or {}).get("message_id") == message_id]
        record = records.get(str(message_id)) if message_id else None
        verdict = workflow.parse_verdict(record.get("reply", "")) if record else None
        failed = [e for e in results if e.status == "error"]
        runs.append({
            "id": message_id or f"run-{start.seq}",
            "status": "running" if finish is None else ("error" if finish.status == "error"
                                                         else meta.get("status", "success")),
            "started_at": start.ts.isoformat(),
            "finished_at": finish.ts.isoformat() if finish else None,
            "duration_s": meta.get("duration_s"),
            "agent_id": start.metadata.get("agent_id"),
            "thread_id": meta.get("thread_id"),
            "message_id": message_id,
            "cost": meta.get("cost"),
            "retrieval": meta.get("retrieval"),
            "mcp_calls": calls,
            "classifications": {e.subject_id: e.metadata.get("category") for e in results
                                if e.type == "agent.classification"},
            "proposals": [e.subject_id for e in results if e.type == "proposal.created"],
            "errors": [e.error for e in failed if e.error],
            "event_range": [start.seq, max([finish.seq if finish else start.seq]
                                           + [e.seq for e in results])],
            "record_file": record.get("file") if record else None,
            "verdict": verdict,
            "explanation": _explanation(record.get("reply", "")) if record else None,
            "trace": _trace(record.get("events") or []) if record else [],
        })
    return list(reversed(runs))


def _proposal_summary(p: Any) -> dict[str, Any]:
    return {"id": p.id, "status": p.status, "base_version": p.base_version,
            "policy_version": p.policy_version, "created_at": p.created_at.isoformat(),
            "created_by": p.created_by}


def impact_snapshot(store: Store, policy_id: str, runs_dir: Path = RUNS_DIR) -> dict[str, Any]:
    history = store.policy_history(policy_id)
    if not history:
        raise workflow.NotFound(f"unknown policy {policy_id!r}")
    policy = history[-1]
    events = store.events(limit=100_000)
    runs = agent_runs(store, runs_dir, events)
    runs_by_id = {r["id"]: r for r in runs}
    latest: dict[str, Event] = {}
    for e in events:
        if e.type == "agent.classification" and e.subject_id:
            latest[e.subject_id] = e
    candidates = {c.document_id: c for c in workflow.detect_candidates(store, policy_id)}
    proposals = store.proposals()

    documents = []
    for doc in store.documents():
        finding = None
        ev = latest.get(doc.id)
        if ev is not None:
            prov = ev.metadata.get("provenance") or {}
            run = runs_by_id.get(str(prov.get("message_id")))
            verdict = (run or {}).get("verdict") or {}
            entry = (verdict.get("documents") or {}).get(doc.id) or {}
            doc_version = ev.metadata.get("document_version", entry.get("document_version"))
            policy_version = ev.metadata.get("policy_version",
                                             (verdict.get("policy") or {}).get("version"))
            evidence = entry.get("evidence") if isinstance(entry.get("evidence"), str) else None
            analysed = (store.document(doc.id, doc_version)
                        if type(doc_version) is int else None)
            finding = {
                "category": ev.metadata.get("category"),
                "classified_at": ev.ts.isoformat(),
                "run_id": prov.get("message_id"),
                "thread_id": prov.get("thread_id"),
                "agent_id": prov.get("agent_id"),
                "document_version": doc_version,
                "policy_version": policy_version,
                "evidence": evidence,
                "evidence_verified": bool(evidence and analysed and evidence in analysed.content),
                "reason": entry.get("reason") if isinstance(entry.get("reason"), str) else None,
                "prefilter": ev.metadata.get("deterministic_scan"),
                "agrees_with_prefilter": ev.metadata.get("agrees_with_scan"),
                "current": doc_version == doc.version and policy_version == policy.version,
            }
        c = candidates.get(doc.id)
        documents.append({
            **doc.model_dump(mode="json"),
            "finding": finding,
            "prefilter": {"state": "actionable" if c and c.actionable else
                          "protected" if c else "not_stale",
                          "stale_sentences": c.stale_sentences if c else [],
                          "protected": {str(k): v for k, v in c.protected.items()} if c else {}},
            "proposals": [_proposal_summary(p) for p in proposals if p.document_id == doc.id],
        })
    with store.read() as conn:
        title = conn.execute("SELECT title FROM policies WHERE id = ?", (policy_id,)).fetchone()[0]
        created = {r["version"]: (r["created_at"], r["created_by"]) for r in conn.execute(
            "SELECT version, created_at, created_by FROM policy_versions WHERE policy_id = ?",
            (policy_id,))}
        counts = dict(conn.execute(
            "SELECT 'events', COUNT(*) FROM events UNION ALL "
            "SELECT 'mcp_calls', COUNT(*) FROM events WHERE type = 'mcp.tool_call' UNION ALL "
            "SELECT 'latest_seq', COALESCE(MAX(seq), 0) FROM events").fetchall())
    return {
        "policy": policy.model_dump(mode="json"),
        "policy_title": title,
        "policy_versions": [{**p.model_dump(mode="json"), "created_at": created[p.version][0],
                             "created_by": created[p.version][1]} for p in history],
        "activity": counts,
        "runs_total": len(runs),
        "channels": store.channels(),
        "documents": documents,
        "latest_run": {k: v for k, v in runs[0].items()
                       if k not in {"verdict", "explanation", "trace"}} if runs else None,
    }
