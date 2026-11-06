"""UI projections of canonical SQLite runs, findings and proposals.

JSON files enrich optional execution traces and pre-v3 archival quotes. New product
state, identities and evidence never depend on those files or event ordering.
Expired capabilities and abandoned runner leases are reaped when runs are queried.
"""

from __future__ import annotations

import json
from functools import wraps
from pathlib import Path
from typing import Any

import config
import workflow
from models import Event
from store import Store

RUNS_DIR = config.ROOT / "runs"
_record_cache: dict[Path, tuple[float, dict[str, Any] | None]] = {}


def _load_record(path: Path) -> dict[str, Any] | None:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    cached = _record_cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        record = record if isinstance(record, dict) else None
        if record is not None:
            if not isinstance(record.get('provenance'), dict):
                record = None
            else:
                record = {**record,
                    'reply': record.get('reply') if isinstance(record.get('reply'), str) else '',
                    'events': record.get('events') if isinstance(record.get('events'), list) else []}
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
        if not isinstance(e, dict):
            continue
        data = e.get("data") or {}
        if not isinstance(data, dict):
            continue
        if e.get("status") not in ("success", "error") or e.get("name") not in ("llm", "tool_call"):
            continue
        steps.append({"kind": e["name"], "status": e["status"], "tool": data.get("name"),
                      "model": data.get("model"),
                      "description": config.safe_text(str(data.get("description") or ""), 400)})
    return steps


def legacy_entry(record: dict[str, Any], document_id: str) -> dict[str, Any]:
    verdict = workflow.parse_verdict(record.get('reply', '')) or {}
    documents = verdict.get('documents')
    entry = documents.get(document_id) if isinstance(documents, dict) else None
    return entry if isinstance(entry, dict) else {}


def _call(e: Event) -> dict[str, Any]:
    m = e.metadata
    return {"seq": e.seq, "ts": e.ts.isoformat(), "status": e.status, "tool": m.get("tool"),
            "document_id": m.get("document_id"), "policy_id": m.get("policy_id"),
            "version": m.get("version"), "secret_slot": m.get("secret_slot"),
            "user_agent": m.get("user_agent"), "duration_ms": m.get("duration_ms"),
            "error": e.error}


def event_run_id(event: Event, runs: list[Any]) -> str | None:
    """Explicit correlation only, except for frozen pre-v3 archival windows."""
    rid = event.metadata.get('run_id') or (event.metadata.get('provenance') or {}).get('run_id')
    if rid:
        return rid
    mid = event.metadata.get('message_id') or (event.metadata.get('provenance') or {}).get('message_id')
    for run in runs:
        if run.metadata.get('legacy'):
            if mid and run.message_id == mid:
                return run.id
            start, finish = run.metadata.get('legacy_start_seq'), run.metadata.get('legacy_finish_seq')
            if start is not None and start <= event.seq <= (finish or start):
                return run.id
    return None


def consistent_projection(fn):
    @wraps(fn)
    def project(store, *args, **kwargs):
        if store._snapshot.get() is None:
            store.reap_runs()
        with store.snapshot():
            return fn(store, *args, **kwargs)
    return project


@consistent_projection
def agent_runs(store: Store, runs_dir: Path = RUNS_DIR,
               events: list[Event] | None = None) -> list[dict[str, Any]]:
    """Persisted runs/findings are authoritative. Files only enrich archival traces."""
    events = events if events is not None else store.events(limit=100_000)
    records = run_records(runs_dir)
    states = store.runs()
    result = []
    for run in reversed(states):
        related = [e for e in events if event_run_id(e, states) == run.id]
        findings = store.findings(run_id=run.id)
        meta = run.metadata
        record = records.get(run.message_id or '')
        seqs = [e.seq for e in related]
        from clarification import phase
        current_phase = phase(store, run.id)
        result.append({
            'id': run.id, 'status': run.status, 'policy_id': run.policy_id,
            'policy_version': run.policy_version, 'document_ids': run.document_ids,
            'started_at': run.created_at.isoformat(),
            'finished_at': run.closed_at.isoformat() if run.closed_at else None,
            'expires_at': run.expires_at.isoformat(), 'duration_s': meta.get('duration_s'),
            'agent_id': run.agent_id, 'thread_id': run.thread_id, 'message_id': run.message_id,
            'conversation_id': current_phase['conversation_id'] if current_phase else None,
            'phase': current_phase['ordinal'] if current_phase else None,
            'awaiting_clarification': bool(meta.get('awaiting_clarification')),
            'cost': meta.get('cost'), 'retrieval': meta.get('retrieval') or workflow.retrieval_status(store, run),
            'document_outcomes': meta.get('document_outcomes', {}),
            'mcp_calls': [_call(e) for e in related if e.type == 'mcp.tool_call'],
            'classifications': {f.document_id: f.category for f in findings},
            'proposals': [p.id for p in store.proposals() if p.run_id == run.id],
            'errors': [run.error] if run.error else [],
            'event_range': [min(seqs), max(seqs)] if seqs else [0, 0],
            'record_file': record.get('file') if record else None,
            'verdict': workflow.parse_verdict(record.get('reply', '')) if record else None,
            'explanation': _explanation(record.get('reply', '')) if record else None,
            'trace': _trace(record.get('events') or []) if record else [],
        })
    return result


def _proposal_summary(p: Any) -> dict[str, Any]:
    return {"id": p.id, "status": p.status, "base_version": p.base_version,
            "policy_version": p.policy_version, "created_at": p.created_at.isoformat(),
            "created_by": p.created_by}


@consistent_projection
def impact_snapshot(store: Store, policy_id: str, runs_dir: Path = RUNS_DIR) -> dict[str, Any]:
    history = store.policy_history(policy_id)
    if not history:
        raise workflow.NotFound(f"unknown policy {policy_id!r}")
    policy = history[-1]
    events = store.events(limit=100_000)
    runs = [r for r in agent_runs(store, runs_dir, events) if r['policy_id'] == policy_id]
    states = {r.id: r for r in store.runs()}
    candidates = {c.document_id: c for c in workflow.detect_candidates(store, policy_id)}
    proposals = store.proposals()
    findings = [f for f in store.findings() if f.policy_id == policy_id]
    records = run_records(runs_dir)
    documents = []
    for doc in store.documents():
        finding = None
        matches = [f for f in findings if f.document_id == doc.id]
        if matches:
            # Only reconciled, authenticated conclusions outrank provisional findings.
            f = max(matches, key=lambda f: (
                f.policy_version == policy.version and (f.document_version == doc.version
                    or (f.category == 'UNKNOWN' and states[f.run_id].document_versions.get(doc.id) == doc.version)),
                workflow.verified_finding(states[f.run_id], f),
                f.category != 'UNKNOWN', f.created_at))
            run = states[f.run_id]
            evidence, reason = f.evidence, f.reason
            if f.source == 'legacy':
                record = records.get(run.message_id or '') or {}
                entry = legacy_entry(record, doc.id)
                evidence = entry.get('evidence') if isinstance(entry.get('evidence'), str) else evidence
                reason = entry.get('reason') if isinstance(entry.get('reason'), str) else reason
            analysed = store.document(doc.id, f.document_version) if f.document_version is not None else None
            c = candidates.get(doc.id)
            scan = 'actionable' if c and c.actionable else 'protected' if c else 'not_stale'
            finding = {
                'verified_conclusion': workflow.verified_finding(run, f),
                'action': (run.metadata.get('document_outcomes', {}).get(doc.id) or {}).get('action'),
                'id': f.id, 'category': f.category, 'classified_at': f.created_at.isoformat(),
                'run_id': f.run_id, 'run_status': run.status, 'thread_id': run.thread_id,
                'agent_id': run.agent_id, 'document_version': f.document_version,
                'policy_version': f.policy_version, 'evidence': evidence or None,
                'evidence_verified': bool(evidence and analysed and evidence in analysed.content),
                'retrieved_during_run': f.retrieved_during_run, 'source': f.source,
                'reason': reason or None, 'prefilter': scan,
                'agrees_with_prefilter': (f.category == 'OUTDATED') == (scan == 'actionable'),
                'current': f.policy_version == policy.version and (
                    f.document_version == doc.version or (f.category == 'UNKNOWN'
                    and run.document_versions.get(doc.id) == doc.version)),
            }
        c = candidates.get(doc.id)
        documents.append({
            **doc.model_dump(mode="json"),
            "finding": finding,
            "provisional_findings": [f.model_dump(mode='json') | {'run_status': states[f.run_id].status}
                for f in reversed(matches) if not workflow.verified_finding(states[f.run_id], f)],
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
        "active_run_ids": [r['id'] for r in runs if r['status'] == 'open'],
        "channels": store.channels(),
        "documents": documents,
        "latest_run": {k: v for k, v in runs[0].items()
                       if k not in {"verdict", "explanation", "trace"}} if runs else None,
    }
