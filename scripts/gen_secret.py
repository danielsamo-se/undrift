"""Create .env (from .env.example) and manage its random secrets. No value is ever printed.

    python scripts/gen_secret.py                 # fill MCP_PATH_SECRET if empty
    python scripts/gen_secret.py --review-token  # fill UNDRIFT_REVIEW_TOKEN if empty
    python scripts/gen_secret.py --stage         # rotation step 1: write MCP_PATH_SECRET_NEXT
    python scripts/gen_secret.py --promote       # rotation step 3: NEXT becomes current
    python scripts/gen_secret.py --rotate        # replace immediately (breaks the live URL)

Rotation with overlap (see SETUP.md): stage, restart server.py (it then accepts both paths),
point the Cominty integration at the next URL, confirm traffic on the `next` slot, promote,
restart again. Only the final restart invalidates the old path.
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


def new_secret() -> str:
    return secrets.token_urlsafe(32)  # 256 bits


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--rotate", action="store_true")
    group.add_argument("--stage", action="store_true")
    group.add_argument("--promote", action="store_true")
    group.add_argument("--review-token", action="store_true")
    args = parser.parse_args()

    if not ENV.exists():
        ENV.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
        print("Created .env from .env.example")

    current = read_value(ENV, "MCP_PATH_SECRET")
    staged = read_value(ENV, "MCP_PATH_SECRET_NEXT")

    if args.review_token:
        if read_value(ENV, "UNDRIFT_REVIEW_TOKEN"):
            print("UNDRIFT_REVIEW_TOKEN already set; left unchanged.")
        else:
            set_value(ENV, "UNDRIFT_REVIEW_TOKEN", new_secret())
            print("UNDRIFT_REVIEW_TOKEN written to .env (value not shown).")
        return 0
    if args.stage:
        if staged:
            print("MCP_PATH_SECRET_NEXT already staged; left unchanged.")
            return 0
        if not current:
            print("No current MCP_PATH_SECRET: run without flags instead.")
            return 1
        set_value(ENV, "MCP_PATH_SECRET_NEXT", new_secret())
        print("MCP_PATH_SECRET_NEXT written to .env (value not shown). The running server ignores "
              "it until restarted; after a restart both the current and the next path work.")
        return 0
    if args.promote:
        if not staged:
            print("Nothing staged: run --stage first.")
            return 1
        set_value(ENV, "MCP_PATH_SECRET", staged)
        set_value(ENV, "MCP_PATH_SECRET_NEXT", "")
        print("Promoted MCP_PATH_SECRET_NEXT to MCP_PATH_SECRET. Restart server.py to make the "
              "old path return 404.")
        return 0

    if current and not args.rotate:
        print("MCP_PATH_SECRET already set; left unchanged (use --stage to rotate with overlap).")
        return 0
    set_value(ENV, "MCP_PATH_SECRET", new_secret())
    set_value(ENV, "MCP_PATH_SECRET_NEXT", "")
    print("MCP_PATH_SECRET written to .env (value not shown).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
