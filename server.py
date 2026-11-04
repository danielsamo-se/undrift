"""Undrift preflight MCP server (Streamable HTTP, read-only, fictional Shiftly data).

Run:  python server.py
The only route is /mcp/<MCP_PATH_SECRET>; every other path returns 404.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

import uvicorn
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field
from starlette.types import ASGIApp, Receive, Scope, Send

import config
from fixtures import DOCUMENTS, POLICIES, Document, Policy

SERVER_NAME = "undrift-shiftly"
SERVER_VERSION = "0.1.0"

log = logging.getLogger("undrift")

READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)

Identifier = Annotated[str, Field(min_length=1, max_length=64)]


class Health(BaseModel):
    status: Literal["ok"]
    server: str
    version: str
    read_only: bool
    tools: list[str]
    policy_ids: list[str]
    document_ids: list[str]
    server_time: datetime


mcp = MCPServer(
    SERVER_NAME,
    title="Undrift MCP (Shiftly preflight)",
    version=SERVER_VERSION,
    instructions=(
        "Read-only access to Shiftly's current return policy and to published content "
        "documents (fictional test data). Call health_check to discover available ids."
    ),
)


@mcp.tool(annotations=READ_ONLY)
def health_check() -> Health:
    """Report server status and the policy and document ids that can be retrieved."""
    log.info("tool health_check")
    return Health(
        status="ok",
        server=SERVER_NAME,
        version=SERVER_VERSION,
        read_only=True,
        tools=["health_check", "get_policy", "get_document"],
        policy_ids=sorted(POLICIES),
        document_ids=sorted(DOCUMENTS),
        server_time=datetime.now(UTC),
    )


@mcp.tool(annotations=READ_ONLY)
def get_policy(
    policy_id: Annotated[Identifier, Field(description="Policy id, e.g. 'shiftly-returns'")],
) -> Policy:
    """Return the company's current, authoritative policy with the given id."""
    log.info("tool get_policy policy_id=%r", policy_id)
    policy = POLICIES.get(policy_id)
    if policy is None:
        raise ToolError(f"Unknown policy_id {policy_id!r}. Known ids: {sorted(POLICIES)}")
    return policy


@mcp.tool(annotations=READ_ONLY)
def get_document(
    document_id: Annotated[Identifier, Field(description="Document id, e.g. 'doc-001'")],
) -> Document:
    """Return one published content document (channel, title, content, version, updated_at)."""
    log.info("tool get_document document_id=%r", document_id)
    document = DOCUMENTS.get(document_id)
    if document is None:
        raise ToolError(f"Unknown document_id {document_id!r}. Known ids: {sorted(DOCUMENTS)}")
    return document


class RedactedAccessLog:
    """Minimal access log that never writes the secret path segment.

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


def build_app(secret: str, public_host: str | None) -> ASGIApp:
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
    return RedactedAccessLog(app, secret)


def main() -> None:
    config.load_env()
    # force=True: MCPServer() already called basicConfig with a bare "%(message)s" format;
    # timestamps are needed to correlate server-side tool calls with Cominty events.
    logging.basicConfig(level=logging.INFO, force=True,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    secret = config.path_secret()
    host = config.public_host()
    port = int(os.environ.get("MCP_PORT", config.DEFAULT_PORT))

    log.info("Undrift MCP %s listening on http://127.0.0.1:%d%s", SERVER_VERSION, port,
             config.mcp_path("<redacted>"))
    log.info("Allowed public host: %s", host or "(none: localhost only)")

    uvicorn.run(
        build_app(secret, host),
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
