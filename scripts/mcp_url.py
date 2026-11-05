"""Show (redacted) or copy (full) the public MCP URL; optionally record the tunnel host.

    python scripts/mcp_url.py --set-host abc-def.trycloudflare.com   # writes MCP_PUBLIC_HOST to .env
    python scripts/mcp_url.py                                        # prints the URL, secret redacted
    python scripts/mcp_url.py --copy                                 # copies the FULL URL to the clipboard
    python scripts/mcp_url.py --copy --next                          # same, for the staged rotation secret

The full URL is never printed. Paste it from the clipboard straight into the
Cominty "MCP Server URL" field.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import config  # noqa: E402
from scripts.envfile import set_value  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--set-host", metavar="HOST", help="tunnel hostname or https URL")
    parser.add_argument("--copy", action="store_true", help="copy the full URL to the clipboard")
    parser.add_argument("--next", action="store_true", help="use MCP_PATH_SECRET_NEXT")
    args = parser.parse_args()

    if args.set_host:
        raw = args.set_host.strip()
        host = (urlparse(raw).hostname if "://" in raw else raw.split("/")[0]).lower()
        set_value(config.ENV_FILE, "MCP_PUBLIC_HOST", host)
        print(f"MCP_PUBLIC_HOST={host} written to .env. Restart server.py to allow this host.")

    config.load_env()
    secret = config.path_secret()
    if args.next:
        secret = config.next_path_secret()
        if not secret:
            raise SystemExit("MCP_PATH_SECRET_NEXT is not staged. Run gen_secret.py --stage.")
    host = config.public_host()
    if not host:
        raise SystemExit("MCP_PUBLIC_HOST is not set. Use --set-host <tunnel hostname>.")
    url = f"https://{host}{config.mcp_path(secret)}"
    print(f"Public MCP URL ({'next' if args.next else 'current'} secret): "
          f"https://{host}{config.mcp_path('<redacted>')}")
    if args.copy:
        subprocess.run(["clip"], input=url, text=True, check=True)
        print("Full URL copied to the clipboard (not shown).")


if __name__ == "__main__":
    main()
