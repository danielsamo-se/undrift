# Shiftly: human review → GitHub PR → Vercel → public verification

The implementation is local. No remote repository, PR, merge or Vercel project has
been created by this work. Obtain explicit approval before initial remote setup.

## Repository and content boundary

`shiftly/` is a standalone, dependency-free Node static site. Copy the **contents**
of this directory into a dedicated Shiftly repository after approval. This keeps
the publishing credential scoped to the fictional storefront, not the Undrift source.
The build command is `npm run build`; output directory is `dist`; no install command
is needed. Node 22 or later is required. Keep `content/returns.txt` byte-for-byte:

```
All customers can return items within 60 days.
```

The checked-in file intentionally has no trailing newline, matching the existing
agent proposal's original text. A newline, whitespace edit or other content change
is considered stale. Never silently normalize it to make publishing pass.

The build escapes the text into `#returns-policy` on `/returns/` and copies the
original UTF-8 bytes to `/content/returns.txt`. `build.json` records
`VERCEL_GIT_COMMIT_SHA` and the content SHA-256. The storefront does not receive any
GitHub credential, reviewer token, Cominty credential, or Undrift database.

## Local checks and preview

From the repository root in PowerShell:

```powershell
$env:Path = "$PWD\.tools\node;$env:Path"
New-Item -ItemType Directory -Force .test-tmp | Out-Null
$testDir = ".test-tmp\publishing-" + [guid]::NewGuid().ToString("N")
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=$testDir
Push-Location shiftly
npm test
npm run build
npm run preview  # http://127.0.0.1:8200 and /returns/
Pop-Location
```

Build the Undrift UI separately while existing demo services are running:

```powershell
Push-Location ui
npm test
npm run typecheck
npm run build -- --outDir ../.test-tmp/publishing-ui-dist
Pop-Location
```

This avoids replacing `ui/dist` while the old API is serving it. Existing processes
do not acquire the new API endpoints until deliberately relaunched.

Prepare a new database containing the existing real agent proposal:

```powershell
.\.venv\Scripts\python.exe scripts\prepare_publishing_demo.py
```

The command uses SQLite's read-only source/backup mechanism, refuses to overwrite
an existing destination, and migrates only the new copy. It never approves a proposal.
Keep the original database and live-findings demo separate. Use the printed new
database path for a separate API process. Do not point the new API at the original.

## Remote setup requires approval

1. Choose the GitHub owner and dedicated repository name; approve creation and
   the initial push of the reviewed `shiftly/` files. No secrets or databases belong
   in that repository. An existing repository can also be used if it contains the
   exact expected file and matching storefront build contract.
2. Import that repository into a Vercel project. Use the production `main` branch,
   build `npm run build`, output `dist`, and root directory `.`. Allow Vercel's GitHub
   integration to create deployment records/statuses. Disable auto-merge on GitHub.
   A human must merge each PR. Protect `main` with PR review as appropriate.
3. Confirm the initial **production** Returns page shows 60 days. Obtain the
   production `https://PROJECT.vercel.app` URL. Preview deployments do not count.
4. If using Vercel Hobby, ensure the GitHub commit author is permitted to deploy
   into that account/team. Do not assume a bot author or arbitrary organization
   author will be accepted. Check Vercel's current account and deployment limits.
5. Create a fine-grained GitHub token limited to this repository with Contents:
   read/write, Pull requests: read/write, and Deployments: read. Metadata read is
   implicit. No Actions administration, secrets administration, merge automation
   or organization-wide access is required. A GitHub App installation token with
   equivalent permissions is also supported; token renewal is an operator concern.

Store the token privately in an ignored file outside the storefront repo:

```json
{"github_token": "<paste privately; never into chat>"}
```

Restrict that file's filesystem permissions to your Windows user. Do not put the
token in `.env`, a frontend environment variable, MCP configuration or an agent prompt.
The publishing adapter reads this file on demand; the MCP process never imports
the adapter or reads the credential file. A connected GitHub plugin alone does not
give the standalone Undrift API a credential.

## Start the separate publishing API after target setup

Use process-local settings in a new terminal; preserve existing `.env` credentials:

```powershell
$env:UNDRIFT_DB = '<new publishing demo path printed above>'
$env:UNDRIFT_PUBLISH_DB = "$PWD\data\publishing-journal.db"
$env:UNDRIFT_GITHUB_REPOSITORY = '<owner>/shiftly-storefront'
$env:UNDRIFT_GITHUB_BASE_BRANCH = 'main'
$env:UNDRIFT_GITHUB_CONTENT_PATH = 'content/returns.txt'
$env:UNDRIFT_PUBLISH_DOCUMENT_ID = 'doc-001'
$env:UNDRIFT_STOREFRONT_URL = 'https://<project>.vercel.app'
$env:UNDRIFT_PUBLISH_CREDENTIAL_FILE = "$PWD\.tools\publishing-credentials.json"
```

To serve the separately built UI on port 8301 without replacing existing services:

```powershell
@'
import config
config.load_env()
import api, uvicorn
from pathlib import Path
from store import Store
store = Store()
store.init()
uvicorn.run(api.build_app(store, ui_dir=Path('.test-tmp/publishing-ui-dist')),
            host='127.0.0.1', port=8301, access_log=False, proxy_headers=False)
'@ | .\.venv\Scripts\python.exe -
```

Open `http://127.0.0.1:8301/#/review`. Unlock with the existing local reviewer token
(copy it with `cli.py copy-token` only in a terminal whose UNDRIFT_DB is the new copy).

## Human authorization and publishing flow

1. Review the agent's exact diff and approve the proposal. This alone creates no PR.
2. Choose **Review repository change**. The server verifies that the configured
   regular file in GitHub equals the proposal's original text, and that local
   document/policy versions remain current. The dialog shows repository, path,
   base commit and exact diff.
3. Choose **Authorize & create PR**. The authorization digest binds database,
   proposal, reviewer decision, repository, branch, file, base commit/blob/tree,
   and original/proposed content. Any changed plan requires a new explicit decision.
4. The adapter creates a deterministic branch containing one exact file change.
   It checks the complete resulting tree and its parent, verifies file bytes,
   recovers existing PRs after ambiguous responses, and never force-pushes or merges.
   Concurrent calls serialize through the journal; retries reuse the same PR.
5. Open the PR, review, then merge manually on GitHub. Changes to the PR head after
   authorization block tracking; Undrift will not bless an altered PR.
6. Vercel automatically builds the merge through its normal Git integration.
7. Choose **Check merge and deployment**. If the session is locked, choose
   **Unlock reviewer session to check deployment**, use the existing reviewer
   token, then explicitly request the check. Reloading the page locks the session.
   Undrift validates the PR identity and exact merged file, then checks the
   configured production site's `build.json`, raw policy bytes and visible Returns
   policy. All three must match the merge commit and exact approved correction
   before either deployed or verified is recorded. GitHub Deployment records are
   optional supplemental evidence; an existing latest production record must be
   successful. A commit status alone never establishes production verification.
   The journal records actual production evidence, including observed/expected
   commit, content hashes, HTTP statuses, source URL and observation time. It never
   invents a GitHub deployment ID when the provider supplies none.

Statuses are separate: approved → PR created → merged → deployed → verified.
Errors produce a blocked state or keep merged/unverified with an explanatory
message. Failed verification never reports current content as verified. The audit
trail is displayed in the Review Queue. Publication does not call the local Apply
action or fabricate a new agent finding; publishing status and local document
version remain distinct. Do not apply locally before starting this workflow.

## Operational limits

- No webhooks or autonomous merging. Status checks are explicit human-triggered reads.
- The first implementation accepts production `*.vercel.app` origins only, with no
  redirects; custom domains need a deliberate verification policy extension.
- Verification requires a public production site. Deployment Protection/login,
  redirects, a different build commit or mismatching raw/rendered policy keeps
  verification pending. Missing GitHub Deployment records do not prevent direct
  verification of the configured production site. Failed checks remove prior
  verification evidence, including after a rollback or a changed PR head.
- Network writes can succeed even if their response is lost. Authorization is saved
  before network mutations; retry the same job, never manually retarget its branch.
- Base branch movement before authorization is a conflict. Once authorized, a job's
  target is immutable. If a stale job has not produced a PR, do not edit its journal
  to bypass the conflict; reanalyse/review a new proposal against current content.
- A reviewer can revoke a proposal before publication starts; once a PR exists,
  close it on GitHub if withdrawing the authorization. Rejection cannot undo a remote
  operation already authorized and in flight. Protect repository access accordingly.
- Keep the journal with the application database backups; its database ID separates
  proposals from different stores. Read-only database copies retain that identity,
  so use a separate journal per demonstration copy if you want independent runs.
- This feature has protocol-level offline tests. Initial real GitHub/Vercel behavior
  must be verified after account/repository/deployment approval. No fixture tests
  establish that a provider account is connected or configured correctly.
