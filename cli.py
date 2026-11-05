"""Local operator CLI for the Undrift demo state.

    python cli.py init                         # create + seed data/undrift.db (idempotent)
    python cli.py change-policy --shiftly-30   # the 60 -> 30 day change, effective 2026-11-04
    python cli.py status                       # policy, impact candidates, proposals
    python cli.py events [--after N] [--type proposal.]
    python cli.py show <proposal_id>
    python cli.py approve|reject <proposal_id> [--note ...]
    python cli.py apply <proposal_id>
    python cli.py copy-token                   # review token to the clipboard (never printed)

Review commands read UNDRIFT_REVIEW_TOKEN from the environment or .env; they fail without it.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date

import config
import fixtures
import workflow
from store import Store


def show_candidates(candidates: list[workflow.Candidate]) -> None:
    if not candidates:
        print("  no document states the superseded value")
    for c in candidates:
        state = "ACTIONABLE" if c.actionable else "protected: " + ", ".join(sorted(set(c.protected.values())))
        print(f"  {c.document_id} v{c.version} ({c.channel}): stale sentences {c.stale_sentences} -> {state}")


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    cp = sub.add_parser("change-policy")
    cp.add_argument("--policy", default=fixtures.POLICY_ID)
    cp.add_argument("--shiftly-30", action="store_true", help="the documented Shiftly scenario")
    cp.add_argument("--days", type=int)
    cp.add_argument("--effective-from", type=date.fromisoformat)
    cp.add_argument("--description")
    sub.add_parser("status")
    sub.add_parser("copy-token")
    ev = sub.add_parser("events")
    ev.add_argument("--after", type=int, default=0)
    ev.add_argument("--limit", type=int, default=50)
    ev.add_argument("--type")
    sh = sub.add_parser("show")
    sh.add_argument("proposal_id")
    for name in ("approve", "reject", "apply"):
        p = sub.add_parser(name)
        p.add_argument("proposal_id")
        if name != "apply":
            p.add_argument("--note")
    args = parser.parse_args()

    config.load_env()
    store = Store()
    if store.init():
        print(f"Created and seeded {store.path}")
    token = os.environ.get("UNDRIFT_REVIEW_TOKEN")

    try:
        if args.cmd == "init":
            print(f"Database ready: {store.path}")
        elif args.cmd == "change-policy":
            change = dict(fixtures.SHIFTLY_RETURNS_CHANGE) if args.shiftly_30 else {}
            if args.days is not None:
                change["voluntary_return_days"] = args.days
            if args.effective_from:
                change["effective_from"] = args.effective_from
            if args.description:
                change["description"] = args.description
            if set(change) != {"voluntary_return_days", "effective_from", "description"}:
                parser.error("use --shiftly-30 or give --days, --effective-from and --description")
            policy, candidates = workflow.change_policy(store, args.policy, token=token, **change)
            print(f"{policy.id} is now v{policy.version}: {policy.voluntary_return_days} days "
                  f"from {policy.effective_from}")
            show_candidates(candidates)
        elif args.cmd == "status":
            for pid in store.policy_ids():
                p = store.policy(pid)
                print(f"policy {pid} v{p.version}: {p.voluntary_return_days} days from {p.effective_from}")
                show_candidates(workflow.detect_candidates(store, pid))
            for d in store.documents():
                print(f"document {d.id} v{d.version} [{d.channel}] {d.title}")
            for p in store.proposals():
                print(f"proposal {p.id} {p.status:<14} {p.document_id} v{p.base_version} "
                      f"policy v{p.policy_version} by {p.created_by}")
        elif args.cmd == "copy-token":
            if len(token or "") < config.MIN_SECRET_LENGTH:
                raise workflow.Unauthorized("UNDRIFT_REVIEW_TOKEN is not configured")
            subprocess.run(["clip"], input=token, text=True, check=True)
            print("Review token copied to the clipboard (not shown). Paste it into the UI's "
                  "reviewer dialog; it is kept in memory only.")
        elif args.cmd == "events":
            for e in store.events(after=args.after, limit=args.limit, type_prefix=args.type):
                subject = f"{e.subject_type}:{e.subject_id}" if e.subject_type else "-"
                print(f"{e.seq:>5} {e.ts:%Y-%m-%d %H:%M:%S.%f} {e.status:<6} {e.type:<28} "
                      f"{subject:<20} {e.actor} {json.dumps(e.metadata)}"
                      + (f" ERROR: {e.error}" if e.error else ""))
        elif args.cmd == "show":
            p = store.proposal(args.proposal_id)
            if p is None:
                raise workflow.NotFound(f"unknown proposal {args.proposal_id!r}")
            print(f"{p.id} [{p.status}] {p.document_id} v{p.base_version} -> policy "
                  f"{p.policy_id} v{p.policy_version}\nby {p.created_by}\nreason: {p.reason}\n")
            print(p.diff)
        else:
            fn = {"approve": workflow.approve, "reject": workflow.reject}.get(args.cmd)
            p = (fn(store, args.proposal_id, token=token, note=args.note) if fn
                 else workflow.apply(store, args.proposal_id, token=token))
            print(f"{p.id} -> {p.status}"
                  + (f" (document {p.document_id} now v{p.applied_version})" if p.applied_version else ""))
    except workflow.WorkflowError as exc:
        print(f"{exc.code}: {exc}" + (f" {json.dumps(config.safe_metadata(exc.details))}"
                                      if exc.details else ""), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
