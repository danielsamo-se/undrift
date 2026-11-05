"""Undrift MCP server (Streamable HTTP, fictional Shiftly data backed by SQLite).

Run:  python server.py
The only route is /mcp/<MCP_PATH_SECRET> (plus /mcp/<MCP_PATH_SECRET_NEXT> while a rotation
is staged); every other path returns 404. Every tool call is recorded in the activity log.
The proposal-submitting write tool exists only with UNDRIFT_MCP_WRITE_TOOLS=1 and never
while MCP_PUBLIC_HOST is set.
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
SERVER_VERSION = "0.2.0"
SLOT_HEADER = b"x-undrift-secret-slot"
READ_TOOLS = ["health_check", "get_policy", "get_document"]

log = logging.getLogger("undrift")

READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)
SUBMIT_ONLY = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False
)

Identifier = Annotated[str, Field(min_length=1, max_length=64)]

_state: dict[str, Any] = {"store": None, "write_tools": False}


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
    read_only: bool
    tools: list[str]
    policy_ids: list[str]
    document_ids: list[str]
    server_time: datetime


class ProposalReceipt(BaseModel):
    proposal_id: str
    status: str
    document_id: str
    base_version: int
    policy_id: str
    policy_version: int
    diff: str


mcp = MCPServer(
    SERVER_NAME,
    title="Undrift MCP (Shiftly)",
    version=SERVER_VERSION,
    instructions=(
        "Read-only access to Shiftly's current return policy and to published content "
        "documents (fictional test data). Call health_check to discover available ids."
    ),
)


@contextmanager
def instrumented(ctx: Context | None, tool: str, subject: tuple[str, str] | None,
                 **meta: Any) -> Iterator[dict[str, Any]]:
    """Log and persist one tool call. Persists only ids, versions and timings."""
    log.info("tool %s %s", tool, " ".join(f"{k}={v!r}" for k, v in meta.items()))
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
            read_only=not _state["write_tools"],
            tools=READ_TOOLS + (["submit_proposal"] if _state["write_tools"] else []),
            policy_ids=store().policy_ids(),
            document_ids=store().document_ids(),
            server_time=datetime.now(UTC),
        )


@mcp.tool(annotations=READ_ONLY)
def get_policy(
    policy_id: Annotated[Identifier, Field(description="Policy id, e.g. 'shiftly-returns'")],
    ctx: Context,
) -> Policy:
    """Return the company's current, authoritative policy with the given id."""
    with instrumented(ctx, "get_policy", ("policy", policy_id), policy_id=policy_id) as meta:
        policy = store().policy(policy_id)
        if policy is None:
            raise ToolError(f"Unknown policy_id {policy_id!r}. Known ids: {store().policy_ids()}")
        meta["version"] = policy.version
        return policy


@mcp.tool(annotations=READ_ONLY)
def get_document(
    document_id: Annotated[Identifier, Field(description="Document id, e.g. 'doc-001'")],
    ctx: Context,
) -> Document:
    """Return one published content document (channel, title, content, version, updated_at)."""
    with instrumented(ctx, "get_document", ("document", document_id),
                      document_id=document_id) as meta:
        document = store().document(document_id)
        if document is None:
            raise ToolError(
                f"Unknown document_id {document_id!r}. Known ids: {store().document_ids()}")
        meta["version"] = document.version
        return document


def submit_proposal(
    document_id: Annotated[Identifier, Field(description="Document to correct")],
    document_version: Annotated[int, Field(description="Version you read with get_document")],
    policy_id: Annotated[Identifier, Field(description="Policy the correction implements")],
    policy_version: Annotated[int, Field(description="Policy version you read with get_policy")],
    proposed_content: Annotated[str, Field(min_length=1, max_length=workflow.MAX_CONTENT,
                                           description="Full corrected document text")],
    reason: Annotated[str, Field(min_length=1, max_length=workflow.MAX_REASON,
                                 description="Why the document must change")],
    ctx: Context,
) -> ProposalReceipt:
    """Submit a correction for human review. It does not change the document; a reviewer must
    approve and apply it outside of MCP."""
    with instrumented(ctx, "submit_proposal", ("document", document_id), document_id=document_id,
                      document_version=document_version, policy_version=policy_version) as meta:
        try:
            p = workflow.submit_proposal(
                store(), document_id=document_id, base_version=document_version,
                policy_id=policy_id, policy_version=policy_version,
                proposed_content=proposed_content, reason=reason,
                actor=f"mcp:{meta['user_agent']}", provenance={"source": "mcp"})
        except workflow.WorkflowError as exc:
            raise ToolError(f"{exc.code}: {exc}") from exc
        meta["proposal_id"] = p.id
        return ProposalReceipt(proposal_id=p.id, status=p.status, document_id=p.document_id,
                               base_version=p.base_version, policy_id=p.policy_id,
                               policy_version=p.policy_version, diff=p.diff)


def enable_write_tools(public_host: str | None) -> None:
    if public_host:
        raise SystemExit("Refusing UNDRIFT_MCP_WRITE_TOOLS=1 while MCP_PUBLIC_HOST is set: the "
                         "shared-secret URL is not an adequate credential for write access.")
    if not _state["write_tools"]:
        mcp.add_tool(submit_proposal, annotations=SUBMIT_ONLY)
        _state["write_tools"] = True


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
                headers.get("host", "-"),
                headers.get("user-agent", "-")[:80],
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
    if config.write_tools_enabled():
        enable_write_tools(host)

    log.info("Undrift MCP %s listening on http://127.0.0.1:%d%s", SERVER_VERSION, port,
             config.mcp_path("<redacted>"))
    log.info("Allowed public host: %s", host or "(none: localhost only)")
    log.info("Staged next secret path: %s", "accepted" if next_secret else "none")
    log.info("Write tools: %s", "ENABLED (local only)" if _state["write_tools"] else "disabled")
    s.record_event("server.started", actor="system", metadata={
        "version": SERVER_VERSION, "public_host": host, "next_secret_staged": bool(next_secret),
        "write_tools": _state["write_tools"]})

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
