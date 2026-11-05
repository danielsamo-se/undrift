"""Local configuration: a tiny .env loader plus redaction helpers.

No python-dotenv dependency: the format we need is `KEY=value` lines only.
Values already present in the process environment always win over .env.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"

DEFAULT_PORT = 8000
DEFAULT_API_PORT = 8100
MCP_BASE_PATH = "/mcp"
MIN_SECRET_LENGTH = 32
SECRET_ENV_NAMES = ("MCP_PATH_SECRET", "MCP_PATH_SECRET_NEXT", "COMINTY_API_KEY",
                    "UNDRIFT_REVIEW_TOKEN")

_MCP_PATH_RE = re.compile(r"/mcp/[A-Za-z0-9_\-]{16,}")
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_\-.~+/=]{8,}")
_SECRET_KEY_RE = re.compile(r"(?i)(^|_)(secret|token|api_?key|password|authorization|cookie|"
                            r"credentials?|mcp_url)$")
_CONTENT_KEY_RE = re.compile(r"(?i)(^|_)(content|text|body|evidence|reply|prompt|note|diff)$")
MAX_METADATA_STRING = 300
MAX_METADATA_ITEMS = 50


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


def _checked_secret(name: str, required: bool) -> str | None:
    secret = os.environ.get(name, "").strip()
    if not secret and not required:
        return None
    if len(secret) < MIN_SECRET_LENGTH:
        raise SystemExit(
            f"{name} is missing or shorter than {MIN_SECRET_LENGTH} characters. "
            "Run: python scripts/gen_secret.py"
        )
    return secret


def path_secret() -> str:
    """The secret MCP path segment. Raises if it is missing or too short."""
    return _checked_secret("MCP_PATH_SECRET", required=True)  # type: ignore[return-value]


def next_path_secret() -> str | None:
    """A staged replacement secret, accepted alongside the current one during rotation."""
    secret = _checked_secret("MCP_PATH_SECRET_NEXT", required=False)
    if secret is not None and secret == os.environ.get("MCP_PATH_SECRET", "").strip():
        raise SystemExit("MCP_PATH_SECRET_NEXT equals MCP_PATH_SECRET; stage a new value.")
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


def db_path() -> Path:
    return Path(os.environ.get("UNDRIFT_DB", "").strip() or ROOT / "data" / "undrift.db")


def write_tools_enabled() -> bool:
    return os.environ.get("UNDRIFT_MCP_WRITE_TOOLS", "0").strip() == "1"


def known_secrets() -> list[str]:
    values = (os.environ.get(name, "").strip() for name in SECRET_ENV_NAMES)
    return [v for v in values if len(v) >= 8]


def redact(text: str, secret: str | None = None) -> str:
    """Replace known secrets, any /mcp/<segment> path and bearer tokens wherever they appear."""
    values = known_secrets() + ([secret] if secret else [])
    for value in sorted(set(values), key=len, reverse=True):
        text = text.replace(value, "<redacted>")
    text = _MCP_PATH_RE.sub(f"{MCP_BASE_PATH}/<redacted>", text)
    return _BEARER_RE.sub("Bearer <redacted>", text)


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def safe_text(text: str, limit: int = MAX_METADATA_STRING) -> str:
    text = redact(text)
    return text if len(text) <= limit else text[:limit] + "...<truncated>"


def safe_metadata(value: Any, _depth: int = 0) -> Any:
    """Make event metadata safe to persist: no secrets, no document contents, bounded size."""
    if _depth > 4:
        return "<nested>"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in list(value.items())[:MAX_METADATA_ITEMS]:
            key = str(key)
            if _SECRET_KEY_RE.search(key):
                out[key] = "<redacted>"
            elif _CONTENT_KEY_RE.search(key) and isinstance(item, str):
                out[key] = {"omitted": "content", "length": len(item), "sha256": fingerprint(item)}
            else:
                out[key] = safe_metadata(item, _depth + 1)
        return out
    if isinstance(value, list | tuple | set):
        return [safe_metadata(v, _depth + 1) for v in list(value)[:MAX_METADATA_ITEMS]]
    if isinstance(value, str):
        return safe_text(value)
    if value is None or isinstance(value, bool | int | float):
        return value
    return safe_text(str(value))
