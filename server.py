"""Undrift MCP server (Streamable HTTP, fictional Shiftly data backed by SQLite).

Run:  python server.py
The only route is /mcp/<MCP_PATH_SECRET> (plus /mcp/<MCP_PATH_SECRET_NEXT> while a rotation
is staged); every other path returns 404. Every tool call is recorded in the activity log.

Optional tools, all off by default:
- record_finding (UNDRIFT_MCP_FINDINGS=1): records one classification per document for an
  open agent run. Requires that run's token, issued locally by run_impact.py. With
  MCP_PUBLIC_HOST set it additionally needs UNDRIFT_MCP_FINDINGS_PUBLIC=1.
- Direct submit_proposal access is retired: UNDRIFT_MCP_WRITE_TOOLS=1 fails closed.
No MCP tool can approve, apply or publish anything.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

import uvicorn
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field
from starlette.types import ASGIApp, Receive, Scope, Send

import config
import workflow
from models import Document, Policy
from store import Store

SERVER_NAME = "undrift-shiftly"
SERVER_VERSION = "0.3.0"
SLOT_HEADER = b"x-undrift-secret-slot"
READ_TOOLS = ["health_check", "get_policy", "get_document"]

log = logging.getLogger("undrift")

READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)
Identifier = Annotated[str, Field(min_length=1, max_length=64)]

_state: dict[str, Any] = {"store": None, "findings_tool": False}
CATEGORY = Literal["OUTDATED", "VALID_DISTINCT", "EXISTING_COMMITMENT", "UNRELATED", "UNKNOWN"]
RECORD_ONLY = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)


def store() -> Store:
    if _state["store"] is None:
        _state["store"] = Store()
    return _state["store"]


def use_store(s: Store) -> None:
    _state["store"] = s


class Health(BaseModel):
    status: Literal["ok"]
    server: str
    version: str
    database_id: str
    read_only: bool
    tools: list[str]
    policy_ids: list[str]
    document_ids: list[str]
    server_time: datetime


class FindingReceipt(BaseModel):
    finding_id: str
    status: Literal["recorded", "duplicate"]
    run_id: str
    document_id: str
    document_version: int | None
    classification: str
    recorded_at: datetime


mcp = MCPServer(
    SERVER_NAME,
    title="Undrift MCP (Shiftly)",
    version=SERVER_VERSION,
    instructions=(
        "Read Shiftly's policy and documents (fictional data). Authenticated runs must pass "
        "run_id and run_token to every read. When enabled, record_finding stores individual "
        "results; only the trusted runner can finalize proposals for human review. "
        "Call health_check to discover available tools and ids."
    ),
)


@contextmanager
def instrumented(ctx: Context | None, tool: str, subject: tuple[str, str] | None,
                 **meta: Any) -> Iterator[dict[str, Any]]:
    """Log and persist one tool call. Persists only ids, versions and timings."""
    log.info("tool %s %s", tool, config.safe_text(" ".join(f"{k}={v!r}" for k, v in meta.items()), 400))
    try:
        headers = (ctx.headers if ctx else None) or {}
    except ValueError:
        headers = {}
    user_agent = str(headers.get("user-agent", "-"))[:80]
    meta.update(tool=tool, user_agent=user_agent,
                secret_slot=headers.get(SLOT_HEADER.decode(), "unknown"))
    started = time.perf_counter()
    error: Exception | None = None
    try:
        yield meta
    except Exception as exc:
        error = exc
        raise
    finally:
        meta["duration_ms"] = round((time.perf_counter() - started) * 1000, 1)
        try:
            store().record_event("mcp.tool_call", actor=f"mcp:{user_agent}",
                                 status="error" if error else "ok", subject=subject,
                                 metadata=meta, error=str(error) if error else None)
        except Exception as exc:  # the audit log must never take a read tool down
            log.warning("activity event not recorded: %s", type(exc).__name__)


@mcp.tool(annotations=READ_ONLY)
def health_check(ctx: Context) -> Health:
    """Report server status and the policy and document ids that can be retrieved."""
    with instrumented(ctx, "health_check", None):
        return Health(
            status="ok",
            server=SERVER_NAME,
            version=SERVER_VERSION,
            database_id=store().identity(),
            read_only=not _state["findings_tool"],
            tools=READ_TOOLS + (["record_finding"] if _state["findings_tool"] else []),
            policy_ids=store().policy_ids(),
            document_ids=store().document_ids(),
            server_time=datetime.now(UTC),
        )


@mcp.tool(annotations=READ_ONLY)
def get_policy(
    policy_id: Annotated[Identifier, Field(description="Policy id, e.g. 'shiftly-returns'")],
    ctx: Context,
    run_id: Annotated[str | None, Field(max_length=40)] = None,
    run_token: Annotated[str | None, Field(max_length=128)] = None,
) -> Policy:
    """Return the company's current, authoritative policy with the given id."""
    with instrumented(ctx, "get_policy", ("policy", policy_id), policy_id=policy_id) as meta:
        policy = _read_for_run("policy", policy_id, run_id, run_token, meta)
        if policy is None:
            raise ToolError(config.redact(f"Unknown policy_id {policy_id!r}. Known ids: {store().policy_ids()}"))
        meta["version"] = policy.version
        return policy


@mcp.tool(annotations=READ_ONLY)
def get_document(
    document_id: Annotated[Identifier, Field(description="Document id, e.g. 'doc-001'")],
    ctx: Context,
    run_id: Annotated[str | None, Field(max_length=40)] = None,
    run_token: Annotated[str | None, Field(max_length=128)] = None,
) -> Document:
    """Return one published content document (channel, title, content, version, updated_at)."""
    with instrumented(ctx, "get_document", ("document", document_id),
                      document_id=document_id) as meta:
        document = _read_for_run("document", document_id, run_id, run_token, meta)
        if document is None:
            raise ToolError(config.redact(
                f"Unknown document_id {document_id!r}. Known ids: {store().document_ids()}"))
        meta["version"] = document.version
        return document


def _read_for_run(kind: str, subject_id: str, run_id: str | None,
                  run_token: str | None, meta: dict[str, Any]) -> Any:
    if run_id is None and run_token is None:
        return store().policy(subject_id) if kind == "policy" else store().document(subject_id)
    if not run_id or not run_token:
        raise ToolError("run_id and run_token must be supplied together")
    try:
        value = workflow.retrieve_for_run(store(), run_id=run_id, run_token=run_token,
            kind=kind, subject_id=subject_id, actor=f"mcp:{meta['user_agent']}")
    except workflow.WorkflowError as exc:
        raise ToolError(f"{exc.code}: {exc}") from exc
    meta['run_id'] = run_id
    return value


def record_finding(
    run_id: Annotated[str, Field(min_length=1, max_length=40, description="Run ID from the task")],
    run_token: Annotated[str, Field(min_length=1, max_length=128,
                                    description="Run token from the task, copied exactly")],
    policy_id: Annotated[Identifier, Field(description="Policy id you retrieved")],
    policy_version: Annotated[int | None, Field(strict=True, description="Retrieved policy version; null only for UNKNOWN after policy retrieval failure")],
    document_id: Annotated[Identifier, Field(description="Document you classified")],
    document_version: Annotated[int | None, Field(strict=True, description="Retrieved version; null only for UNKNOWN after retrieval failure")],
    classification: Annotated[CATEGORY, Field(description="Exactly one category")],
    evidence: Annotated[str, Field(max_length=1000,
                                   description="Exact sentence quoted from the retrieved content; "
                                               "empty only for UNKNOWN")],
    reason: Annotated[str, Field(min_length=1, max_length=1000,
                                 description="Which policy field or rule justifies the category")],
    ctx: Context,
) -> FindingReceipt:
    """Record your classification of ONE document for the current analysis run. Call it once per
    document, after retrieving the policy and that document. It only stores a finding for human
    review: it cannot change documents, proposals or policies, and cannot approve or publish."""
    with instrumented(ctx, "record_finding", ("document", document_id),
                      document_id=document_id, document_version=document_version,
                      classification=classification) as meta:
        try:
            finding, duplicate = workflow.record_finding(
                store(), run_id=run_id, run_token=run_token, policy_id=policy_id,
                policy_version=policy_version, document_id=document_id,
                document_version=document_version, classification=classification,
                evidence=evidence, reason=reason, actor=f"mcp:{meta['user_agent']}")
        except workflow.WorkflowError as exc:
            raise ToolError(f"{exc.code}: {exc}") from exc
        meta.update(finding_id=finding.id, duplicate=duplicate, run_id=finding.run_id)
        return FindingReceipt(finding_id=finding.id, status="duplicate" if duplicate else "recorded",
                              run_id=finding.run_id, document_id=finding.document_id,
                              document_version=finding.document_version,
                              classification=finding.category, recorded_at=finding.created_at)


def enable_findings_tool(public_host: str | None) -> None:
    if public_host and not config.findings_public_allowed():
        raise SystemExit("Refusing UNDRIFT_MCP_FINDINGS=1 on a public host without "
                         "UNDRIFT_MCP_FINDINGS_PUBLIC=1 (see SETUP.md, record_finding).")
    if not _state["findings_tool"]:
        mcp.add_tool(record_finding, annotations=RECORD_ONLY)
        _state["findings_tool"] = True


def enable_write_tools(public_host: str | None) -> None:
    # A direct MCP proposal could be submitted before a run fails or finishes.
    # Preserve the old flag as a fail-closed configuration error, never a bypass.
    raise SystemExit("Direct MCP proposal submission is disabled. Use record_finding and "
                     "trusted runner finalization after verified retrieval.")


class SecretRotation:
    """Serve the staged next secret path as an alias of the current one, and tag each request
    with the slot it used (clients cannot set the tag themselves)."""

    def __init__(self, app: ASGIApp, current: str, next_: str | None) -> None:
        self.app = app
        self.current_path = config.mcp_path(current)
        self.next_path = config.mcp_path(next_) if next_ else None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = [(k, v) for k, v in scope["headers"] if k.lower() != SLOT_HEADER]
            slot = b"current" if scope["path"] == self.current_path else b"none"
            if self.next_path and scope["path"] == self.next_path:
                scope = {**scope, "path": self.current_path,
                         "raw_path": self.current_path.encode()}
                slot = b"next"
            scope = {**scope, "headers": [*headers, (SLOT_HEADER, slot)]}
        await self.app(scope, receive, send)


class RedactedAccessLog:
    """Minimal access log that never writes a secret path segment.

    Replaces uvicorn's access log, which would print the full request path.
    """

    def __init__(self, app: ASGIApp, secret: str) -> None:
        self.app = app
        self.secret = secret

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        status: dict[str, Any] = {"code": None}

        async def send_wrapper(message: Any) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]}
            log.info(
                "%s %s -> %s %.0fms host=%s ua=%r",
                scope["method"],
                config.redact(scope["path"], self.secret),
                status["code"],
                (time.perf_counter() - started) * 1000,
                config.safe_text(headers.get("host", "-")),
                config.safe_text(headers.get("user-agent", "-")[:80]),
            )


def build_app(secret: str, public_host: str | None, next_secret: str | None = None) -> ASGIApp:
    allowed_hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    allowed_origins = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
    if public_host:
        allowed_hosts.append(public_host)
        allowed_origins.append(f"https://{public_host}")
    extra_origins = os.environ.get("MCP_EXTRA_ALLOWED_ORIGINS", "")
    allowed_origins += [o.strip() for o in extra_origins.split(",") if o.strip()]

    app = mcp.streamable_http_app(
        streamable_http_path=config.mcp_path(secret),
        # Stateless + JSON responses: each POST is self-contained, which avoids
        # session affinity and SSE buffering issues behind a tunnel/proxy.
        stateless_http=os.environ.get("MCP_STATELESS", "1") == "1",
        json_response=os.environ.get("MCP_JSON_RESPONSE", "1") == "1",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=allowed_hosts,
            allowed_origins=allowed_origins,
        ),
    )
    return RedactedAccessLog(SecretRotation(app, secret, next_secret), secret)


def main() -> None:
    config.load_env()
    # force=True: MCPServer() already called basicConfig with a bare "%(message)s" format;
    # timestamps are needed to correlate server-side tool calls with Cominty events.
    logging.basicConfig(level=logging.INFO, force=True,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    secret = config.path_secret()
    next_secret = config.next_path_secret()
    host = config.public_host()
    port = int(os.environ.get("MCP_PORT", config.DEFAULT_PORT))

    s = Store()
    if s.init():
        log.info("Initialized and seeded a new database")
    use_store(s)
    s.reap_runs()
    if config.write_tools_enabled():
        enable_write_tools(host)
    if config.findings_tool_enabled():
        enable_findings_tool(host)

    log.info("Undrift MCP %s listening on http://127.0.0.1:%d%s", SERVER_VERSION, port,
             config.mcp_path("<redacted>"))
    log.info("Allowed public host: %s", host or "(none: localhost only)")
    log.info("Staged next secret path: %s", "accepted" if next_secret else "none")
    log.info("Write tools: disabled (proposals require trusted run finalization)")
    log.info("record_finding: %s", ("ENABLED" + (" on public host" if host else " (local only)"))
             if _state["findings_tool"] else "disabled")
    s.record_event("server.started", actor="system", metadata={
        "version": SERVER_VERSION, "public_host": host, "next_secret_staged": bool(next_secret),
        "write_tools": False, "findings_tool": _state["findings_tool"]})

    uvicorn.run(
        build_app(secret, host, next_secret),
        host="127.0.0.1",  # cloudflared connects locally; never bind publicly
        port=port,
        access_log=False,  # would print the secret path; RedactedAccessLog replaces it
        server_header=False,
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
        log_level="info",
    )


if __name__ == "__main__":
    main()
