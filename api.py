"""Local-only review and activity API (JSON) plus the built React UI (ui/dist).

    python api.py        # UI on http://127.0.0.1:8100/, JSON under /api/...

Binds 127.0.0.1 only and rejects any request whose client address or Host header is not
loopback, so it cannot be served through a tunnel. Reads need no token; every state change
needs `Authorization: Bearer <UNDRIFT_REVIEW_TOKEN>`. There is no endpoint for agents.
"""

from __future__ import annotations

import ipaddress
import os
from datetime import date
from pathlib import Path
from typing import Any

import uvicorn
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.types import ASGIApp, Message, Receive, Scope, Send

import config
import clarification
import readmodel
import workflow
from store import Store

LOOPBACK_ORIGIN_RE = r"^http://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$"
UI_DIST = config.ROOT / "ui" / "dist"
MAX_EVENTS = 1000
SECURITY_HEADERS = [
    (b"content-security-policy", b"default-src 'self'; script-src 'self'; style-src 'self' "
     b"'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; connect-src 'self'; "
     b"object-src 'none'; base-uri 'none'; frame-ancestors 'none'"),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
]


class ReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str | None = Field(default=None, max_length=workflow.MAX_REASON)


class EmptyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClarificationAnswerBody(BaseModel):
    model_config = ConfigDict(extra='forbid')
    option: int | None = Field(default=None, ge=0, strict=True)
    text: str | None = Field(default=None, min_length=1, max_length=4000)


class PolicyChangeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1, strict=True)
    voluntary_return_days: int
    effective_from: date
    description: str = Field(min_length=1, max_length=4000)


class PublishBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approval_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


def _is_loopback_host(value: str) -> bool:
    host = value.strip().lower()
    if host.startswith("["):
        host = host[1:].split("]", 1)[0]
    elif host.count(":") == 1:
        host = host.split(":", 1)[0]
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class LocalOnly:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            client = (scope.get("client") or ("", 0))[0]
            host = dict(scope["headers"]).get(b"host", b"").decode("latin-1")
            if not _is_loopback_host(client) or not _is_loopback_host(host):
                await JSONResponse({"error": "local access only"}, status_code=403)(
                    scope, receive, send)
                return
        await self.app(scope, receive, send)


class SecurityHeaders:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = [*message.get("headers", []), *SECURITY_HEADERS]
            await send(message)

        await self.app(scope, receive, send_with_headers)


def _token(request: Request) -> str | None:
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    return value.strip() if scheme.lower() == "bearer" and value.strip() else None


async def _body(request: Request, model: type[BaseModel]) -> Any:
    raw = await request.body()
    try:
        return model.model_validate_json(raw or b"{}")
    except ValidationError as exc:
        raise workflow.WorkflowError("invalid request body",
                                     fields=[".".join(map(str, e["loc"])) for e in exc.errors()])


def _legacy_proposal_evidence(proposal: Any, store: Store, runs_dir: Path) -> str:
    # Only historical proposals lack a persisted quote. Never use the latest finding.
    if not proposal.run_id:
        return ""
    run = store.run(proposal.run_id)
    if run is None or not run.metadata.get('legacy'):
        return ""
    record = readmodel.run_records(runs_dir).get(run.message_id or '') or {}
    quote = readmodel.legacy_entry(record, proposal.document_id).get('evidence')
    return quote if isinstance(quote, str) and quote in proposal.original_content else ""


def build_app(store: Store, runs_dir: Path = readmodel.RUNS_DIR,
              ui_dir: Path | None = UI_DIST, publisher=None) -> ASGIApp:
    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "database": store.path.name})

    async def reliability_report(_: Request) -> JSONResponse:
        import reliability
        return JSONResponse(await run_in_threadpool(reliability.load_report, os.environ.get('UNDRIFT_RELIABILITY_REPORT')))

    async def events(request: Request) -> JSONResponse:
        await run_in_threadpool(store.reap_runs)
        q = request.query_params
        subject = (q["subject_type"], q["subject_id"]) if "subject_type" in q and "subject_id" in q else None
        try:
            after, limit = int(q.get("after", 0)), int(q.get("limit", 100))
            tail = int(q["tail"]) if "tail" in q else None
        except ValueError:
            raise workflow.WorkflowError("after, limit and tail must be integers")
        if tail is not None:
            items = store.events(limit=max(1, min(tail, MAX_EVENTS)), type_prefix=q.get("type"),
                                 subject=subject, latest=True)
        else:
            items = store.events(after=after, limit=max(1, min(limit, MAX_EVENTS)),
                                 type_prefix=q.get("type"), subject=subject)
        latest = store.events(limit=1, latest=True)
        states = store.runs()
        serialized = []
        for e in items:
            item = e.model_dump(mode="json")
            rid = readmodel.event_run_id(e, states)
            if rid:
                item['metadata']['run_id'] = rid
            serialized.append(item)
        return JSONResponse({"events": serialized,
                             "next_after": items[-1].seq if items else after,
                             "latest_seq": latest[0].seq if latest else 0})

    async def proposals(request: Request) -> JSONResponse:
        items = store.proposals(status=request.query_params.get("status"),
                                document_id=request.query_params.get("document_id"))
        return JSONResponse({"proposals": [p.model_dump(mode="json") for p in items]})

    async def proposal(request: Request) -> JSONResponse:
        pid = request.path_params["proposal_id"]
        p = store.proposal(pid)
        if p is None:
            raise workflow.NotFound(f"unknown proposal {pid!r}")
        audit = store.events(subject=("proposal", pid), limit=MAX_EVENTS)
        created = next((e for e in audit if e.type == "proposal.created"), None)
        doc, pol = store.document(p.document_id), store.policy(p.policy_id)
        return JSONResponse({
            "proposal": p.model_dump(mode="json"),
            "evidence": p.evidence or _legacy_proposal_evidence(p, store, runs_dir),
            "events": [e.model_dump(mode="json") for e in audit],
            "review_flags": (created.metadata.get("review_flags") or []) if created else [],
            "checks": {
                "document_current_version": doc.version if doc else None,
                "policy_current_version": pol.version if pol else None,
                "document_unchanged": bool(doc and doc.version == p.base_version
                                           and doc.content == p.original_content),
                "policy_current": bool(pol and pol.version == p.policy_version),
            },
        })

    async def documents(_: Request) -> JSONResponse:
        return JSONResponse({"documents": [d.model_dump(mode="json") for d in store.documents()],
                             "channels": store.channels()})

    async def policy(request: Request) -> JSONResponse:
        pid = request.path_params["policy_id"]
        history = store.policy_history(pid)
        if not history:
            raise workflow.NotFound(f"unknown policy {pid!r}")
        return JSONResponse({"current": history[-1].model_dump(mode="json"),
                             "versions": [p.model_dump(mode="json") for p in history],
                             "candidates": [{**c.__dict__, "actionable": c.actionable}
                                            for c in workflow.detect_candidates(store, pid)]})

    async def impact(request: Request) -> JSONResponse:
        return JSONResponse(await run_in_threadpool(readmodel.impact_snapshot, store,
                            request.path_params["policy_id"], runs_dir))

    async def runs(_: Request) -> JSONResponse:
        items = await run_in_threadpool(readmodel.agent_runs, store, runs_dir)
        return JSONResponse({"runs": [{k: v for k, v in r.items()
                                       if k not in {"verdict", "explanation", "trace"}}
                                      for r in items]})

    async def run(request: Request) -> JSONResponse:
        rid = request.path_params["run_id"]
        items = await run_in_threadpool(readmodel.agent_runs, store, runs_dir)
        found = next((r for r in items if r["id"] == rid), None)
        if found is None:
            raise workflow.NotFound(f"unknown run {rid!r}")
        return JSONResponse({"run": found})

    async def session(request: Request) -> JSONResponse:
        reviewer = await run_in_threadpool(workflow.authorize, store, _token(request), "session.verify",
                                           ("session", "reviewer"))
        return JSONResponse({"reviewer": reviewer})

    async def conversations(_: Request) -> JSONResponse:
        await run_in_threadpool(store.reap_runs)
        return JSONResponse({'conversations': await run_in_threadpool(clarification.listing, store)})

    async def answer_question(request: Request) -> JSONResponse:
        body = await _body(request, ClarificationAnswerBody)
        result = await run_in_threadpool(clarification.answer, store, request.path_params['question_id'],
                                        token=_token(request), **body.model_dump())
        return JSONResponse(result)

    async def change_policy(request: Request) -> JSONResponse:
        body = await _body(request, PolicyChangeBody)
        new, candidates = await run_in_threadpool(workflow.change_policy,
            store, request.path_params["policy_id"], token=_token(request), **body.model_dump())
        return JSONResponse({"policy": new.model_dump(mode="json"),
                             "candidates": [{**c.__dict__, "actionable": c.actionable}
                                            for c in candidates]}, status_code=201)

    async def publication(request: Request) -> JSONResponse:
        # Credential loading and journal creation are lazy and API-only. MCP never
        # imports the publishing module or receives its credential file contents.
        def current():
            nonlocal publisher
            if publisher is None:
                import publishing
                publisher = publishing.configured(store)
            return publisher
        service = await run_in_threadpool(current)
        pid = request.path_params['proposal_id']
        if request.method == 'GET':
            state = await run_in_threadpool(service.get, pid) if service else None
            return JSONResponse({'configured': service is not None, 'publication': state})
        # Authenticate even when the target has not yet been configured.
        await run_in_threadpool(workflow.authorize, store, _token(request),
                                'publishing.request', ('proposal', pid))
        if service is None:
            raise workflow.WorkflowError('Configure the publishing target before creating a GitHub PR')
        action = request.path_params['action']
        if action == 'publish':
            body = await _body(request, PublishBody)
            state = await run_in_threadpool(service.publish, pid, body.approval_digest, _token(request))
            return JSONResponse({'publication': state})
        await _body(request, EmptyBody)
        if action == 'plan':
            return JSONResponse({'plan': await run_in_threadpool(service.plan, pid, _token(request))})
        if action == 'refresh':
            return JSONResponse({'publication': await run_in_threadpool(service.refresh, pid, _token(request))})
        raise workflow.NotFound('Unknown publication action')

    def review(action: str):
        async def handler(request: Request) -> JSONResponse:
            pid = request.path_params["proposal_id"]
            if action == "apply":
                await _body(request, EmptyBody)
                p = await run_in_threadpool(workflow.apply, store, pid, token=_token(request))
            else:
                body = await _body(request, ReviewBody)
                fn = workflow.approve if action == "approve" else workflow.reject
                p = await run_in_threadpool(fn, store, pid, token=_token(request), note=body.note)
            return JSONResponse({"proposal": p.model_dump(mode="json")})
        return handler

    async def workflow_error(_: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, workflow.WorkflowError)
        return JSONResponse({"error": exc.code, "message": str(exc),
                             "details": config.safe_metadata(exc.details)}, status_code=exc.status)

    app = Starlette(
        routes=[
            Route("/api/health", health),
            Route('/api/reliability', reliability_report),
            Route("/api/events", events),
            Route("/api/documents", documents),
            Route("/api/policies/{policy_id}", policy),
            Route("/api/policies/{policy_id}/versions", change_policy, methods=["POST"]),
            Route("/api/policies/{policy_id}/impact", impact),
            Route("/api/runs", runs),
            Route("/api/runs/{run_id}", run),
            Route("/api/session", session, methods=["POST"]),
            Route('/api/conversations', conversations),
            Route('/api/questions/{question_id}/answer', answer_question, methods=['POST']),
            Route("/api/proposals", proposals),
            Route("/api/proposals/{proposal_id}", proposal),
            Route("/api/proposals/{proposal_id}/approve", review("approve"), methods=["POST"]),
            Route("/api/proposals/{proposal_id}/reject", review("reject"), methods=["POST"]),
            Route("/api/proposals/{proposal_id}/apply", review("apply"), methods=["POST"]),
            Route("/api/proposals/{proposal_id}/publication", publication),
            Route("/api/proposals/{proposal_id}/publication/{action}", publication, methods=["POST"]),
            *([Mount("/", StaticFiles(directory=ui_dir, html=True))]
              if ui_dir is not None and (ui_dir / "index.html").is_file() else []),
        ],
        exception_handlers={workflow.WorkflowError: workflow_error},
    )
    app.add_middleware(CORSMiddleware, allow_origin_regex=LOOPBACK_ORIGIN_RE,
                       allow_methods=["GET", "POST"], allow_headers=["authorization", "content-type"])
    return LocalOnly(SecurityHeaders(app))


def main() -> None:
    config.load_env()
    store = Store()
    store.init()
    store.reap_runs()
    port = int(os.environ.get("UNDRIFT_API_PORT", config.DEFAULT_API_PORT))
    ui = "UI" if (UI_DIST / "index.html").is_file() else "UI not built yet (see SETUP.md);"
    print(f"Undrift {ui} http://127.0.0.1:{port}/  API http://127.0.0.1:{port}/api (local only)")
    uvicorn.run(build_app(store), host="127.0.0.1", port=port, server_header=False,
                log_level="info")


if __name__ == "__main__":
    main()
