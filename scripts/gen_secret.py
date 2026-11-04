"""Create .env (from .env.example) and fill MCP_PATH_SECRET with a random value.

    python scripts/gen_secret.py            # only if the secret is empty
    python scripts/gen_secret.py --rotate   # replace it (re-register the URL in Cominty after!)

The secret is never printed.
"""

from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.envfile import read_value, set_value  # noqa: E402

ENV = ROOT / ".env"
EXAMPLE = ROOT / ".env.example"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rotate", action="store_true")
    args = parser.parse_args()

    if not ENV.exists():
        ENV.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
        print("Created .env from .env.example")

    if read_value(ENV, "MCP_PATH_SECRET") and not args.rotate:
        print("MCP_PATH_SECRET already set; left unchanged (use --rotate to replace).")
        return
    set_value(ENV, "MCP_PATH_SECRET", secrets.token_urlsafe(32))  # 256 bits
    print("MCP_PATH_SECRET written to .env (value not shown).")


if __name__ == "__main__":
    main()
