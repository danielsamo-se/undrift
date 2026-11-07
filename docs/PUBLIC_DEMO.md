# Public Undrift jury demo

Public URL: **https://undrift-jury-demo.equalglass4.chatgpt.site**

The jury deployment is a real cloud-hosted, read-only historical application. It
uses the existing React interface and genuine Starlette/readmodel projections.
It has no dependency on the laptop, ngrok, local API services, Cominty availability
or GitHub credentials. The operational Python application and local reviewer flow
remain unchanged and separate.

## Release v4 (prepared, not deployed)

The public site still serves v3. A v4 candidate is built and verified **locally only**; publishing needs the owner's approval and the authenticated Codex Sites workflow. Its record is `deployment/jury/release-v4-prepared.json`.

| | |
|---|---|
| Snapshot | `5341fcca1bc0da916563350862d40de901d55be2a39532f643c61b54628b47b9` |
| Worker SHA-256 | `a554f246d1914ec82eb5eebae608a17f9e4f9f3ea916ed86b83cd38ee01b2b41` (about 1.15 MB compressed) |
| Archives | 13 |
| Worker code | Unchanged from v3; same Sites project and `hosting.json` |

What v4 adds:
- **Byte-identical datasets:** 11 of the 12 v3 datasets are copied unchanged. No history is regenerated.
- **`guard`, re-captured:** it now holds both real Cominty Guard verdicts, the ALLOW `gchk-79d5…` and the BLOCK `gchk-5ce8…`. The precheck had returned CLEAR on the BLOCK. Memory and Slack metadata are reused from the v3 capture, so no network call was made.
- **`autonomy-live`, new:** the real delegated Impact run `run-84e7…`. It failed the search-first contract, so the server rejected it and nothing was proposed or applied.
- **UI:**
  - the execution timeline;
  - the Guard ALLOW vs BLOCK comparison;
  - archive hints in place of load errors on Guard and Memory;
  - a 10-step jury guide;
  - honest autonomy evidence links.

Local verification (`.tools/verify_jury_v4.py`) passed:
- all 13 database identities, both Guard verdicts and the failed autonomy run;
- 44 write attempts denied with 403, and private paths returning 404;
- 14 browser routes, including the timeline and the BLOCK;
- preservation. The only changes since the v3 baseline are the disclosed `.env` line, two reliability-v2 runtime attestation files, and one appended `server.started` event in `evaluation-05.db`; every other table is byte-identical.

The 117-page archive × view QA matrix and 33 interactive flows (`.tools/qa/`) found no errors.

**Codex handoff (only after approval):**
1. **Copy the package.** Copy `.tools/jury-site-v4/` into the registered Site checkout `.tools/jury-site`: `dist/`, `worker.mjs`, `replay.json`, `PUBLIC-REPLAY.json` and `package.json`. Keep `.openai/hosting.json`. Commit it as a new source version, and do not rebuild or re-export.
2. **Save and deploy.** Save a new Sites version and deploy exactly that version. Version 3 remains the rollback.
3. **Check the snapshot.** The public `/api/demo` must report snapshot `5341fcca…` with 13 archives.
4. **Verify.** Stop the local preview on :8602, then run `.venv\Scripts\python.exe -B .tools\verify_jury_v4.py https://undrift-jury-demo.equalglass4.chatgpt.site`.
5. **Record the result.** Record the saved version ID in a new `deployment/jury/deployment.json` revision, keeping v3's record under `releases/`.

To rebuild the candidate locally, run `sh .tools/rebuild_jury_v4.sh`. It recreates only the UI and package from the frozen `.tools/jury-replay-v4.json`. To preview it, run `.tools\node\node.exe .tools\jury-preview-v4.mjs`, which serves http://127.0.0.1:8602.

## Release v3 (deployed and verified)

The live site serves **Sites version 3**, published through the authenticated Codex Sites workflow on 7 November 2026. `deployment/jury/deployment.json` binds its saved version ID, source commit, exact Worker bytes and public snapshot. The prepared release record remains unchanged at `deployment/jury/release-v3-prepared.json`.

- Site source commit: `903f76b9f5d1a797b1cccbc63f9292a011f1890a` (the separate Sites repository).
- Undrift implementation checkpoint: `687cabc`.
- Snapshot SHA-256: `ed2dc39075fba64cddcfc85ae3691b276d069980d7139fb7c08a71b4bb3587a3`.
- Worker SHA-256: `4873df971ab70f45a25d4f76cd7a3029e22c13158dfb439bb39fd71d6a3e9234`.
- Exactly 12 historical archives; no writable backend, runtime secrets, MCP server or paid execution.

Public verification passed: all 12 expected database identities, all ten browser views (including mobile), 44 write attempts rejected with 403, private paths rejected with 404, and the recorded Guard, Memory, Autonomy, clarification, publishing and Reliability evidence. Sanitized results are retained in `deployment/jury/verification.json`. The separate Worker suite passed all five tests.

What v3 changes:

- **Landing page:** a new jury landing page with the core message, a before/after policy panel, key metrics, an evidence legend and a nine-step guided journey.
- **Three new archives (12 in total):**
  - `autonomy`: the recorded v6 delegation and audit;
  - `autonomy-fixture`: the scripted fixture, labelled as a fixture in the banner and catalog;
  - `guard`: one real Cominty Guard ALLOW plus deterministic and fail-closed checks, all labelled.
- **Memory and Slack:** a metadata-only read-back of the native Cominty Memory file and the Slack connector status. Slack is not connected.
- **Memory text withheld:** the stored memory interpretation text, which contains a known redaction defect, is not published.
- **Unchanged:** the Worker code. The release only adds read-only datasets.

Build artifacts:
- package: `.tools/jury-site-v3` (git-ignored);
- export: `.tools/jury-replay-v3.json`, snapshot `ed2dc39075fba64c…`;
- verification: `.tools/verify_jury_v3.py`, which checks the 12 database identities, the Guard, Memory and Autonomy evidence, 44 write attempts (all 403), private paths (404) and 10 browser routes, including a phone width.

To recheck this release, run `.venv\Scripts\python.exe -B .tools\verify_jury_v3.py https://undrift-jury-demo.equalglass4.chatgpt.site`.

Version 2 remains saved in the same Sites project for rollback. Its original deployment and verification records are preserved under `deployment/jury/releases/`, and its local source bytes are retained in the ignored `.tools/jury-site-v2-rollback-20261107/`. If rollback is required, deploy the exact v2 saved version ID from `releases/v2-deployment.json` through Sites; do not create another project or rewrite source history. No rollback was performed during v3 publication.

## Architecture and scope

`jury.py` exports twelve independent archives using the existing API handlers and
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

The nine-step guide links discovery, native clarification and same-thread continuation,
exact-patch review, channel autonomy, GitHub PR to Vercel verification, Guard,
Memory and the complete Reliability study. Recorded MCP activity is also accessible.
Each page displays a historical/read-only banner. Archive selection
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
- `autonomy`: recorded channel delegation and audit from the operator environment.
- `autonomy-fixture`: a clearly labelled local fixture demonstrating bounded application.
- `guard`: the real server-verified Cominty ALLOW, labelled deterministic checks,
  native Memory read-back metadata and the disconnected Slack status.

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
The Worker digest is computed from the exact file bytes after writing, including
Windows newline handling, and checked against the saved archive. The first two
deployment versions have identical application archive bytes and snapshot identity;
the second corrects the source build record. No historical evidence was changed.

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
