"""Local configuration: a tiny .env loader plus redaction helpers.

No python-dotenv dependency: the format we need is `KEY=value` lines only.
Values already present in the process environment always win over .env.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"

DEFAULT_PORT = 8000
MCP_BASE_PATH = "/mcp"
MIN_SECRET_LENGTH = 32


def load_env(path: Path = ENV_FILE) -> None:
    """Load KEY=value pairs from .env without overriding real environment variables."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def path_secret() -> str:
    """The secret MCP path segment. Raises if it is missing or too short."""
    secret = os.environ.get("MCP_PATH_SECRET", "").strip()
    if len(secret) < MIN_SECRET_LENGTH:
        raise SystemExit(
            "MCP_PATH_SECRET is missing or shorter than "
            f"{MIN_SECRET_LENGTH} characters. Run: python scripts/gen_secret.py"
        )
    return secret


def mcp_path(secret: str) -> str:
    return f"{MCP_BASE_PATH}/{secret}"


def public_host() -> str | None:
    """Public hostname (no scheme, no path) that the tunnel forwards, e.g. abc.trycloudflare.com."""
    host = os.environ.get("MCP_PUBLIC_HOST", "").strip().lower()
    if not host:
        return None
    if "://" in host or "/" in host:
        raise SystemExit("MCP_PUBLIC_HOST must be a bare hostname, e.g. abc-def.trycloudflare.com")
    return host


def redact(text: str, secret: str | None = None) -> str:
    """Replace the path secret wherever it appears."""
    secret = secret if secret is not None else os.environ.get("MCP_PATH_SECRET", "")
    if secret:
        text = text.replace(secret, "<redacted>")
    return text
