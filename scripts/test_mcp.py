"""Protocol-level MCP tests using the official MCP Python client over Streamable HTTP.

Local (default): builds a throwaway database (seed + the Shiftly policy change), starts
server.py as a subprocess on a free port with throwaway secrets, runs every check, then scans
the server's log output and the database for the secrets.

    python scripts/test_mcp.py

Remote: tests the public tunnel URL built from MCP_PUBLIC_HOST + MCP_PATH_SECRET
(.env or environment). The URL is never printed unredacted.

    python scripts/test_mcp.py --remote
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx
from mcp import Client

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import config  # noqa: E402
import fixtures  # noqa: E402
import workflow  # noqa: E402
from store import Store  # noqa: E402

EXPECTED_TOOLS = {"health_check", "get_policy", "get_document"}
INIT_BODY = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "undrift-http-probe", "version": "0"},
    },
}
SIM_PUBLIC_HOST = "preflight-sim.trycloudflare.com"
POST_HEADERS = {"content-type": "application/json", "accept": "application/json, text/event-stream"}


class Results:
    def __init__(self, secret: str) -> None:
        self.secret = secret
        self.rows: list[dict[str, str]] = []

    def record(self, name: str, ok: bool, evidence: str) -> None:
        evidence = config.redact(evidence, self.secret)
        self.rows.append({"test": name, "status": "PASS" if ok else "FAIL", "evidence": evidence})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {evidence}")

    @property
    def failed(self) -> int:
        return sum(r["status"] == "FAIL" for r in self.rows)


def structured(result: Any) -> dict[str, Any]:
    """Parse a tool result; require structured content and matching JSON text content."""
    assert not result.is_error, f"tool returned is_error: {result.content}"
    data = result.structured_content
    assert isinstance(data, dict), f"no structured_content: {result!r}"
    text_blocks = [c.text for c in result.content if getattr(c, "type", None) == "text"]
    assert text_blocks, "no text content block"
    assert json.loads(text_blocks[0]) == data, "text content JSON != structured_content"
    return data


async def check_mcp(url: str, mode: str, r: Results) -> None:
    tag = f"[{mode}]"
    try:
        async with Client(url, mode=mode, read_timeout_seconds=30) as client:
            info = client.server_info
            r.record(
                f"{tag} protocol initialization",
                info is not None and info.name == "undrift-shiftly",
                f"protocol={client.protocol_version} server={info.name if info else None} "
                f"version={info.version if info else None}",
            )

            listed = await client.list_tools()
            tools = {t.name: t for t in listed.tools}
            r.record(f"{tag} tool discovery", set(tools) == EXPECTED_TOOLS, f"tools={sorted(tools)}")

            schema_problems: list[str] = []
            for name, param in (("get_policy", "policy_id"), ("get_document", "document_id")):
                s = tools[name].input_schema
                prop = s.get("properties", {}).get(param, {})
                if s.get("type") != "object" or s.get("required") != [param]:
                    schema_problems.append(f"{name}: required={s.get('required')}")
                if prop.get("type") != "string" or prop.get("maxLength") != 64:
                    schema_problems.append(f"{name}.{param}: {prop}")
                if set(s.get("properties", {})) != {param, "run_id", "run_token"}:
                    schema_problems.append(f"{name}: extra params {list(s.get('properties', {}))}")
            hs = tools["health_check"].input_schema
            if hs.get("properties") or hs.get("required"):
                schema_problems.append(f"health_check takes params: {hs}")
            for name, t in tools.items():
                if t.output_schema is None:
                    schema_problems.append(f"{name}: no output_schema")
                if not (t.annotations and t.annotations.read_only_hint):
                    schema_problems.append(f"{name}: not annotated read-only")
            doc_out = tools["get_document"].output_schema or {}
            want = {"id", "channel", "title", "content", "version", "updated_at"}
            if set(doc_out.get("required", [])) != want:
                schema_problems.append(f"get_document output required={doc_out.get('required')}")
            pol_out = tools["get_policy"].output_schema or {}
            want = {"id", "version", "policy_type", "voluntary_return_days", "effective_from",
                    "description"}
            if set(pol_out.get("required", [])) != want:
                schema_problems.append(f"get_policy output required={pol_out.get('required')}")
            r.record(
                f"{tag} input/output schemas",
                not schema_problems,
                "; ".join(schema_problems) or "string ids (maxLength 64, required), output schemas "
                "present, all tools readOnlyHint=true",
            )

            h = structured(await client.call_tool("health_check", {}))
            r.record(
                f"{tag} health_check",
                h["status"] == "ok" and h["read_only"] is True and len(h["document_ids"]) == 4,
                f"status={h['status']} read_only={h['read_only']} docs={h['document_ids']}",
            )

            p = structured(await client.call_tool("get_policy", {"policy_id": "shiftly-returns"}))
            r.record(
                f"{tag} get_policy",
                p["voluntary_return_days"] == 30 and p["effective_from"] == "2026-11-04",
                f"voluntary_return_days={p['voluntary_return_days']} "
                f"effective_from={p['effective_from']} version={p['version']}",
            )

            d = structured(await client.call_tool("get_document", {"document_id": "doc-003"}))
            r.record(
                f"{tag} get_document",
                d["id"] == "doc-003" and "#1001" in d["content"] and d["channel"] == "support-crm",
                f"id={d['id']} channel={d['channel']} content={d['content'][:45]!r}...",
            )

            bad = await client.call_tool("get_document", {"document_id": "doc-999"})
            text = " ".join(getattr(c, "text", "") for c in bad.content)
            r.record(
                f"{tag} unknown document -> tool error",
                bad.is_error and "Unknown document_id 'doc-999'" in text,
                f"is_error={bad.is_error} text={text[:90]!r}",
            )

            bad = await client.call_tool("get_policy", {"policy_id": "nope"})
            r.record(
                f"{tag} unknown policy -> tool error",
                bad.is_error,
                f"is_error={bad.is_error}",
            )

            # Schema violations: missing argument, oversize argument.
            outcomes = []
            for args in ({}, {"document_id": "x" * 65}):
                try:
                    res = await client.call_tool("get_document", args)
                    outcomes.append(f"is_error={res.is_error}")
                    ok = res.is_error
                except Exception as exc:  # protocol-level rejection is also acceptable
                    outcomes.append(f"raised {type(exc).__name__}")
                    ok = True
                if not ok:
                    break
            r.record(f"{tag} invalid arguments rejected", ok, "; ".join(outcomes))

            try:
                res = await client.call_tool("delete_document", {"document_id": "doc-001"})
                r.record(f"{tag} unknown tool rejected", res.is_error, f"is_error={res.is_error}")
            except Exception as exc:
                r.record(f"{tag} unknown tool rejected", True, f"raised {type(exc).__name__}")
    except Exception as exc:
        r.record(f"{tag} MCP session", False, f"{type(exc).__name__}: {exc}")


async def check_http(base: str, path: str, r: Results, local: bool) -> None:
    """Raw HTTP checks: routing, 404s, Host/Origin validation."""
    async with httpx.AsyncClient(base_url=base, timeout=20) as http:
        codes = {}
        for p in ("/", "/mcp", "/mcp/", f"/mcp/{secrets.token_urlsafe(32)}", "/docs",
                  "/.well-known/oauth-protected-resource"):
            codes[p] = (await http.get(p)).status_code
        wrong = (await http.post(f"/mcp/{secrets.token_urlsafe(32)}", json=INIT_BODY,
                                 headers=POST_HEADERS)).status_code
        r.record(
            "unknown paths -> 404",
            all(c == 404 for c in codes.values()) and wrong == 404,
            f"GET {sorted(set(codes.values()))} for {len(codes)} paths; POST wrong secret {wrong}",
        )

        ok = (await http.post(path, json=INIT_BODY, headers=POST_HEADERS)).status_code
        r.record("secret path accepts initialize", ok == 200, f"POST {ok}")

        evil_origin = (await http.post(path, json=INIT_BODY,
                                       headers={**POST_HEADERS, "origin": "https://evil.example"}))
        r.record("foreign Origin rejected", evil_origin.status_code == 403,
                 f"POST with Origin evil.example -> {evil_origin.status_code}")

        if local:
            evil_host = await http.post(path, json=INIT_BODY,
                                        headers={**POST_HEADERS, "host": "evil.example"})
            r.record("foreign Host rejected (DNS rebinding)", evil_host.status_code == 421,
                     f"POST with Host evil.example -> {evil_host.status_code}")
            # Simulate what cloudflared forwards: the public hostname in Host.
            tunnel = await http.post(path, json=INIT_BODY,
                                     headers={**POST_HEADERS, "host": SIM_PUBLIC_HOST})
            other = await http.post(path, json=INIT_BODY,
                                    headers={**POST_HEADERS, "host": "other.trycloudflare.com"})
            r.record("allow-listed public Host accepted, other tunnel host rejected",
                     tunnel.status_code == 200 and other.status_code == 421,
                     f"Host {SIM_PUBLIC_HOST} -> {tunnel.status_code}; "
                     f"Host other.trycloudflare.com -> {other.status_code}")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def wait_ready(base: str, proc: subprocess.Popen[bytes], timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    async with httpx.AsyncClient(base_url=base, timeout=2) as http:
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"server exited early with code {proc.returncode}")
            try:
                await http.get("/")
                return
            except httpx.TransportError:
                await asyncio.sleep(0.2)
    raise RuntimeError("server did not become ready")


def prepare_db(path: Path, review_token: str) -> None:
    os.environ["UNDRIFT_REVIEW_TOKEN"] = review_token
    store = Store(path)
    store.init()
    workflow.change_policy(store, fixtures.POLICY_ID, token=review_token,
                           **fixtures.SHIFTLY_RETURNS_CHANGE)


async def run_local() -> Results:
    secret, next_secret = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    r = Results(secret)
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    tmp = Path(tempfile.mkdtemp(prefix="undrift-test-"))
    db = tmp / "undrift.db"
    review_token = secrets.token_urlsafe(32)
    prepare_db(db, review_token)
    env = {**os.environ, "MCP_PATH_SECRET": secret, "MCP_PATH_SECRET_NEXT": next_secret,
           "MCP_PORT": str(port), "MCP_PUBLIC_HOST": SIM_PUBLIC_HOST, "PYTHONUNBUFFERED": "1",
           "UNDRIFT_DB": str(db), "UNDRIFT_MCP_WRITE_TOOLS": "0",
           "UNDRIFT_MCP_FINDINGS": "0", "UNDRIFT_MCP_FINDINGS_PUBLIC": "0",
           "UNDRIFT_REVIEW_TOKEN": review_token}
    log_file = tempfile.NamedTemporaryFile("w+b", suffix=".log", delete=False)
    proc = subprocess.Popen([sys.executable, str(ROOT / "server.py")], cwd=ROOT, env=env,
                            stdout=log_file, stderr=subprocess.STDOUT)
    try:
        await wait_ready(base, proc)
        url = base + config.mcp_path(secret)
        await check_mcp(url, "legacy", r)
        await check_mcp(url, "auto", r)
        await check_http(base, config.mcp_path(secret), r, local=True)
        try:
            async with Client(base + config.mcp_path(next_secret), mode="legacy",
                              read_timeout_seconds=30) as client:
                h = structured(await client.call_tool("health_check", {}))
            r.record("staged next secret path served (rotation overlap)", h["status"] == "ok",
                     f"health_check via next path status={h['status']}")
        except Exception as exc:
            r.record("staged next secret path served (rotation overlap)", False,
                     f"{type(exc).__name__}: {exc}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log_file.close()
    log_text = Path(log_file.name).read_text(encoding="utf-8", errors="replace")
    Path(log_file.name).unlink(missing_ok=True)
    tool_lines = [ln for ln in log_text.splitlines() if "undrift: tool " in ln]
    # Per mode: health_check, get_policy x2, get_document x2 reach the function body; +1 next path.
    r.record("server log shows tool invocations", len(tool_lines) == 11,
             f"{len(tool_lines)} 'undrift: tool ...' log lines (expected 11)")
    r.record("secrets absent from server log",
             all(s not in log_text for s in (secret, next_secret, review_token)),
             f"{len(log_text.splitlines())} log lines scanned")

    calls = Store(db).events(type_prefix="mcp.tool_call", limit=1000)
    slots = sorted({e.metadata.get("secret_slot") for e in calls})
    errors = sorted(f"{e.metadata.get('tool')}" for e in calls if e.status == "error")
    r.record("activity log persisted every tool call",
             len(calls) == 11 and errors == ["get_document", "get_document", "get_policy", "get_policy"]
             and slots == ["current", "next"],
             f"{len(calls)} mcp.tool_call events, errors={errors}, secret slots={slots}")
    raw = b"".join(f.read_bytes() for f in tmp.iterdir())
    r.record("secrets absent from database files",
             all(s.encode() not in raw for s in (secret, next_secret, review_token)),
             f"{len(raw)} bytes scanned")
    shutil.rmtree(tmp, ignore_errors=True)
    return r


async def run_remote() -> Results:
    config.load_env()
    secret = config.path_secret()
    host = config.public_host()
    if not host:
        raise SystemExit("Set MCP_PUBLIC_HOST (e.g. abc.trycloudflare.com) first.")
    r = Results(secret)
    base = f"https://{host}"
    print(f"Remote target: {base}{config.mcp_path('<redacted>')}")
    url = base + config.mcp_path(secret)
    await check_mcp(url, "legacy", r)
    await check_mcp(url, "auto", r)
    await check_http(base, config.mcp_path(secret), r, local=False)
    return r


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--remote", action="store_true", help="test the public tunnel URL")
    args = parser.parse_args()
    r = asyncio.run(run_remote() if args.remote else run_local())
    out = ROOT / "runs" / f"test_mcp_{'remote' if args.remote else 'local'}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "results": r.rows},
                              indent=2), encoding="utf-8")
    print(f"\n{len(r.rows) - r.failed}/{len(r.rows)} passed. Evidence: {out.relative_to(ROOT)}")
    return 1 if r.failed else 0


if __name__ == "__main__":
    sys.exit(main())
