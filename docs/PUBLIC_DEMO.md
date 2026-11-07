# Public Undrift jury demo

Public URL: **https://undrift-jury-demo.equalglass4.chatgpt.site**

The jury deployment is a real cloud-hosted, read-only historical application. It
uses the existing React interface and genuine Starlette/readmodel projections.
It has no dependency on the laptop, ngrok, local API services, Cominty availability
or GitHub credentials. The operational Python application and local reviewer flow
remain unchanged and separate.

## Architecture and scope

`jury.py` exports nine independent archives using the existing API handlers and
readmodel against `ArchiveStore`, a SQLite connection in `mode=ro` with
`query_only=ON`. Migrations, seeding and run reaping are disabled. Export never
changes historical states, imports unrelated runs into one database, or invents
agent activity. It preserves source database identities, source hashes, canonical
run/message references, policy/document versions, findings, exact proposal diffs,
recorded questions/answers, audit history and publication evidence.

The public adapter in `deployment/jury/worker.mjs` serves those immutable projections
and the built React assets from a versioned Cloudflare Worker release hosted through
Sites. These are persistent deployment artifacts, **not a mutable hosted SQLite
database**. This is deliberate: the jury audience requested read-only replay, and
no cloud reviewer token or paid-agent credential is needed. Historical database IDs
identify the original source archives. `snapshot_id` identifies the exported public
release and remains stable across requests and fresh Worker instances.

Live approvals, agent execution, new retrieval, publishing and background jobs are
not deployed. Existing authenticated workflows remain in the operational application.
A future authenticated operator backend can retain the same domain/API contracts;
it needs independently configured identity, durable writable storage and job ownership
before live Guard, channel autonomy, Memory, Slack or memo generation is exposed.

## Jury experience

The guide links six parts of the genuine demonstration: autonomous discovery,
native clarification and same-thread continuation, exact-patch review, human-merged
GitHub PR to Vercel verification, the complete Reliability study, and recorded MCP
activity. Each page displays a historical/read-only banner. Archive selection
remounts the data views and retains a shareable `?replay=` query parameter, so events
and findings from independent executions cannot be merged in the browser.

The archives are:

- `discovery`: original 24-document autonomous discovery, including its mistakes.
- `clarification`: five genuine native questions, local-reviewer answers and the
  successful same-thread continuation.
- `publishing`: the separate publishing demo and its verified publication journal.
- `evaluation-1` through `evaluation-5`: all five real Reliability-v2 trials,
  retaining both strategy failures and their saved questions.
- `reliability-v1`: the archived failed first evaluation, unchanged.

Reliability-v1 and v2 reports remain separately selectable. Primary v2 accuracy
and outdated/UNKNOWN recall average 60%; the two failed trials receive zero primary
classification credit. The replay preserves the complete 3.28553740-unit, eight-message
study. No new events or trial outcomes are created when a visitor opens a page.

The publishing replay retains proposal `prop-04ca726838fc`, real Shiftly PR #1 and
verified production commit `c391b36083663d06bbd0cd98f72806daf005b3e2`. Verification
is displayed with its original timestamp and recorded source; visiting the replay
does not perform another production check. The actual Shiftly website already shows
the corrected policy and is never reset by this deployment.

## Access control and secret isolation

- Worker routes accept only GET/HEAD. Every other method returns 403, including
  approval, session unlock, answers, publishing and MCP paths. Supplied Authorization
  headers do not grant write access; no write implementation exists in this target.
- Read routes and static assets use explicit allowlists. Unknown/private paths return
  404, including databases, `.env`, `.tools`, credentials and the evaluation rubric.
- Operational write controls are absent in the jury interface. The API client
  also refuses mutations in the public build. Operational builds default to their
  existing authentication behavior.
- The deployed release has no MCP server, tunnel, connector bindings, secret values,
  database bindings, outbound SDK calls or runtime environment configuration.
- Export recursively removes credential-shaped fields and redacts run credentials,
  private MCP paths and known secrets. Original SQLite files, raw provider prompts,
  credentials, answer-book files, ground truth, logs and `.env` are not uploaded.
  Genuine historical questions and their recorded answers are intentionally visible.
- CSP, frame isolation, nosniff and referrer headers are applied. API responses are
  not cached as live mutable state; hashed static assets are immutable.

## Reproduction

Run from the repository root using the existing isolated toolchain:

```powershell
# Each export output is exclusive; choose a NEW filename for a future capture.
.\.venv\Scripts\python.exe -B jury.py --output .tools/jury-replay-next.json

# Jury mode is process-local. Existing operational builds remain unchanged.
$env:VITE_PUBLIC_JURY='1'
Push-Location ui
..\.tools\node\node.exe node_modules/vite/bin/vite.js build --outDir ../.test-tmp/jury-ui-next --logLevel warn
Pop-Location
Remove-Item Env:VITE_PUBLIC_JURY

# The Site checkout is ignored and isolated; preserve its registered project ID.
.\.venv\Scripts\python.exe -B scripts/build_jury_site.py --ui .test-tmp/jury-ui-next --bundle .tools/jury-replay-next.json --site .tools/jury-site

.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider --basetemp=.test-tmp/jury-tests-next
.\.tools\node\node.exe --test deployment/jury/worker.test.mjs
```

Use the Sites source/package/save/deploy workflow for this same registered project.
Keep short-lived repository credentials in memory and send them only through hidden
stdin. The source helper may need a process-local `safe.directory` entry for the
isolated checkout on Windows; do not modify global Git settings. New publication
requires rebuilding and verifying the new immutable snapshot; never overwrite
historical evaluations to manufacture a cleaner demonstration.

The deployment record in `deployment/jury/deployment.json` binds the saved Site
version, source commit, Worker hash and public snapshot identity. Raw exported
projections and deployment archives remain under ignored `.tools/`; sanitized cloud
artifacts are stored by the isolated Site source/version system.

## Verification and limits

Focused Python regressions verify no-write historical access, unchanged archive
bytes, redaction, and reuse of canonical projections. Worker tests reject forged
authorization, private/unknown paths and invalid pagination; they check historical
event sequences and stable identity across fresh instances. React tests verify
archive selection, the replay banner, absent approval controls and blocked mutations.
The full operational suites remain green.

Verification additionally checks all nine public database identities, both Reliability
series, all five actual human clarification answers, the exact recorded production
commit, direct write denial and private-file isolation. Fresh-browser checks cover
the guide, Impact Map, review/publication, Reliability and Activity routes. The local
preview is stopped before public verification.

This read-only release does not demonstrate new live paid agent execution from the
public site. Its evidence is genuine and time-stamped, and future execution still
requires separate authorization. Original archives, credentials, running services
and production Shiftly are preserved. Hosting remains subject to the account's
included service availability and limits; no paid resources were purchased.
