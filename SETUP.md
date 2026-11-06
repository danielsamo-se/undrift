# Undrift: setup

The isolated 24-document unified discovery demo is documented in
[docs/DISCOVERY.md](docs/DISCOVERY.md). Its builder creates a fresh database and
never imports or resets historical evidence. Discovery tools are opt-in and
require a run token; existing running services are unchanged.

All commands run in **PowerShell** from `C:\Users\danie\PycharmProjects\undrift`, using the project venv.
Secrets live only in `.env` (git-ignored). Never paste secrets into chat, commits, or screenshots.

## 0. Python dependencies (done)

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
```

Verified versions: Python 3.13.3, cominty-sdk 1.2.0, mcp 2.3.0, uvicorn 0.54.0.

## 1. Secret endpoint path (done)

```powershell
.\.venv\Scripts\python.exe scripts\gen_secret.py
```

This writes a 256-bit `MCP_PATH_SECRET` to `.env` and never prints it. The endpoint is `/mcp/<secret>`; every other path returns 404.
To rotate without downtime, use the staged procedure in step 12. `--rotate` replaces the secret immediately and breaks the registered URL.

## 2. Local verification (isolated databases; no Cominty calls)

```powershell
.\.venv\Scripts\python.exe scripts\test_mcp.py
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp=.test-tmp\backend-manual
```

`test_mcp.py` builds a throwaway database (seed plus the Shiftly policy change) and never touches `data/undrift.db`.

## 3. Install cloudflared (manual, needs your approval)

cloudflared is **not installed** on this machine. Install it with winget (v1.29 is available):

```powershell
winget install --id Cloudflare.cloudflared
```

Close and reopen PowerShell, then check: `cloudflared --version`.
A Quick Tunnel needs no Cloudflare account.

## 4. Start the tunnel and the server (order matters)

The server only accepts the exact public hostname it is told about (DNS-rebinding protection stays on). So start the tunnel first:

**Terminal A**: the Quick Tunnel. Use `127.0.0.1`, not `localhost`: on Windows `localhost` may resolve to `::1`, and the server listens on IPv4 only.

```powershell
cloudflared tunnel --url http://127.0.0.1:8000
```

The output contains a line like `https://<random-words>.trycloudflare.com`. Keep this terminal open.

**Terminal B**: record the host, then start the server:

```powershell
.\.venv\Scripts\python.exe scripts\mcp_url.py --set-host <random-words>.trycloudflare.com
.\.venv\Scripts\python.exe server.py
```

**Terminal C**: test the public endpoint with a real MCP client:

```powershell
.\.venv\Scripts\python.exe scripts\test_mcp.py --remote
```

> **Quick Tunnel limitation:** every `cloudflared tunnel --url` restart gets a **new random hostname**. After a restart, repeat step 4 (`--set-host`, restart `server.py`) and update the URL in the Cominty integration (step 5). Quick Tunnels are for development only and have no uptime guarantee.

## 5. Register the custom MCP integration in Cominty (manual)

1. Copy the full URL to the clipboard (it is not printed):
   ```powershell
   .\.venv\Scripts\python.exe scripts\mcp_url.py --copy
   ```
2. In the Cominty platform, open the custom MCP integration form and enter:

   | Field | Value |
   |---|---|
   | MCP Server URL | paste from clipboard (`https://<host>.trycloudflare.com/mcp/<secret>`) |
   | Title | `Undrift MCP` |
   | Identifier | `undrift` |
   | Workspace visibility | private / only me (the data is fictional, but the URL is the only credential) |
   | OAuth Client ID / Secret | leave **empty** |

3. Save, and make sure the integration is **enabled** for your user.
4. Watch Terminal B. If Cominty probes the server when you save, you'll see redacted lines like `POST /mcp/<redacted> -> 200 ... ua='...'`.

## 6. Create the custom Managed Agent (manual)

At platform.cominty.ai → **Agents** → create a new agent:

| Setting | Value |
|---|---|
| Name | `Undrift Impact` |
| Instructions | paste the full contents of `agents/impact.md` |
| Model | platform default (or any model you prefer; record which one) |
| MCP | on, with **only** the `Undrift MCP` integration connected |
| Web search, indexed documents, skills, image/video/music generation, memory | off |
| Visibility | private |

`spike.py` also restricts the thread to the `undrift` integration only, with web, documents, skills, and memory off. So a stray platform setting can't add other sources.

## 7. IDs and the API key (manual)

- **Agent ID**: platform.cominty.ai → Agents → your agent → copy its id.
- **API key**: platform.cominty.ai/api-keys → create a key. It is shown **once**, so paste it straight into `.env`.
- **User ID**: avatar (top right) → **Profile**, looks like `user_...`.

## 8. Environment variables

Edit `.env` in PyCharm (not in chat):

```
COMINTY_API_KEY=<your key>
COMINTY_USER_ID=user_...
COMINTY_AGENT_ID=<agent id>
UNDRIFT_MCP_CONNECTION=undrift
```

## 9. Run the spike

```powershell
.\.venv\Scripts\python.exe spike.py          # free: checks key, agent, integration (no agent run)
.\.venv\Scripts\python.exe spike.py --run    # ONE paid run, max_steps=6
```

The free mode lists the integration's name and enabled status. It checks that the registered URL matches `MCP_PUBLIC_HOST` and the secret, without printing the URL.
If `UNDRIFT_EXPECTED_MODEL` is set, the free check also requires the agent's model chain to be exactly that one model; otherwise `--run` refuses to start.
Each paid run saves redacted evidence to `runs/spike_<timestamp>.json`, including the model name reported for each LLM step.

`agents/impact.md` mirrors the instructions stored in Cominty. If you edit them on the platform, re-sync the file.

**Budget:** the Cominty SDK has **no hard spending cap**. `max_steps` caps tool rounds per message, not money, and cost is reported only after the run (`Result.data.cost.total`). Run `--run` once, read the cost, and decide before running again.

## 10. Local state: SQLite

`data/undrift.db` (git-ignored) holds policies with version history, channels, documents with version history, proposals and the activity log. `server.py`, `api.py` and `cli.py` share it (WAL mode). An empty database is seeded with the four preflight documents and policy v1 (60 days).

```powershell
.\.venv\Scripts\python.exe scripts\gen_secret.py --review-token   # once; value not shown
.\.venv\Scripts\python.exe cli.py init
.\.venv\Scripts\python.exe cli.py change-policy --shiftly-30        # v2: 30 days from 2026-11-04
.\.venv\Scripts\python.exe cli.py status
.\.venv\Scripts\python.exe cli.py events
```

Status on 2026-11-05: the database exists, the policy is at v2, and `UNDRIFT_REVIEW_TOKEN` is set. The policy change flagged doc-001 as actionable; doc-003 is flagged but protected as an existing commitment.

## 11. Review: authorization model

- Agents can only **submit** proposals. A proposal is created as `pending_review` and never changes the document.
- Changing a policy, approving, rejecting and applying all require `UNDRIFT_REVIEW_TOKEN`, and it is checked on every call. Anything an agent writes, such as "approved by a human" or `"status": "approved"`, is ignored. MCP has no review tool.
- `apply` checks that the document is still at the proposal's base version with identical content; otherwise it fails with 409. It writes a new document version to the local database only.
- Anyone who can read `.env` on this machine is a reviewer. That is acceptable for the local demo, not for a multi-user deployment.

```powershell
.\.venv\Scripts\python.exe cli.py show <proposal_id>
.\.venv\Scripts\python.exe cli.py approve <proposal_id> --note "checked"
.\.venv\Scripts\python.exe cli.py apply <proposal_id>
```

The local API for the React frontend runs with `python api.py` on `http://127.0.0.1:8100/api`. It refuses any non-loopback client or `Host` header (403), so a tunnel cannot expose it. CORS allows only `http://localhost:*` and `http://127.0.0.1:*`. Reads need no token; every `POST` needs `Authorization: Bearer <UNDRIFT_REVIEW_TOKEN>`.

| Method | Path | |
|---|---|---|
| GET | `/api/events?after=&limit=&type=&subject_type=&subject_id=` | activity log, ordered by `seq`; poll with `after=next_after` |
| GET | `/api/proposals?status=&document_id=` | proposals with original, proposed content and diff |
| GET | `/api/proposals/{id}` | proposal plus its audit events |
| GET | `/api/documents` | current documents and channels |
| GET | `/api/policies/{id}` | current version, history, deterministic impact candidates |
| POST | `/api/policies/{id}/versions` | new policy version (token) |
| POST | `/api/proposals/{id}/approve` / `reject` / `apply` | review actions (token) |

## 12. Rotate the exposed MCP path secret (coordinated, with overlap)

The current secret leaked in a copied Cloudflare error log. A replacement is **already staged** as `MCP_PATH_SECRET_NEXT` in `.env`. The running server ignores it until restarted. During the overlap the new `server.py` serves both paths, and every tool call records which one was used (`secret_slot`: `current` or `next`).

1. *(done)* `python scripts\gen_secret.py --stage`
2. **Restart `server.py`** (Terminal B: Ctrl+C, then `.\.venv\Scripts\python.exe server.py`). The log must show `Staged next secret path: accepted` and `Write tools: disabled`. The old URL keeps working.
3. `python scripts\mcp_url.py --copy --next`, then paste into the Cominty integration's MCP Server URL and save.
4. `python spike.py` (free) must print `path_matches_secret=next`. Then `python cli.py events` should show new `mcp.tool_call` events with `"secret_slot": "next"`.
5. `python scripts\gen_secret.py --promote`, then restart `server.py` again. The old path now returns 404. Run `python scripts\test_mcp.py --remote` to confirm.

Do not do step 5 before step 3: promoting first breaks the Cominty integration until its URL is updated.

## 13. Agent impact + correction run (prepared, not run)

`run_impact.py` asks the agent to classify all documents and write a full corrected text for each OUTDATED one. The reply is parsed locally, and each correction goes through the same validation as every other proposal: optimistic version checks, policy version, guardrails, at most one open proposal per document. It becomes `pending_review`. Nothing is approved or applied.

Before the first paid run:

1. Put the contents of `agents/patch.md` into the agent's instructions, either on the existing agent or on a new agent (then update `COMINTY_AGENT_ID`). This is a remote agent change.
2. `python run_impact.py` (free) must print `instructions ... MATCH`, `model gate ... OK` and `local ... actionable=['doc-001']`.
3. `python run_impact.py --run` performs ONE paid run. Evidence goes to `runs/impact_<timestamp>.json`, and the MCP calls the server saw during the run are listed from the activity log, including calls made by Cominty subagents.

Direct MCP proposal submission is retired. `UNDRIFT_MCP_WRITE_TOOLS=1` now refuses startup, including locally. Only trusted runner finalization can create agent proposals after the entire run passes validation. `record_finding` cannot create proposals.

## 14. Product UI (React + TypeScript)

Node.js is not installed system-wide. A portable Node 24.22.2 LTS (official zip, SHA-256 verified against nodejs.org) lives in the git-ignored `.tools\node`. Put it on the PATH of the terminal you use for the UI:

```powershell
$env:Path = "$PWD\.tools\node;$env:Path"
cd ui
npm ci            # once
npm test          # vitest
npm run build     # type check + production bundle into ui\dist
cd ..
```

Run it (Terminal D). The MCP server and tunnel keep running unchanged; this is a separate, loopback-only process:

```powershell
.\.venv\Scripts\python.exe api.py
```

Open http://127.0.0.1:8100/. To unlock review actions, run `.\.venv\Scripts\python.exe cli.py copy-token` and paste the token into **Reviewer locked**. The browser keeps it in memory only.

For UI development with hot reload, keep `api.py` running and start `npm run dev` in `ui\`, then open http://127.0.0.1:5173 (Vite proxies `/api` to port 8100).

## Later: a stable URL (not configured; ask first)

- **Named Cloudflare Tunnel** with a custom domain: needs a Cloudflare account and a domain on Cloudflare. `cloudflared tunnel login`, `cloudflared tunnel create undrift`, then a DNS route such as `mcp.<yourdomain>` and a `config.yml` ingress to `http://127.0.0.1:8000`. The hostname stays stable across restarts, so the Cominty URL never changes. The server still runs on this PC.
- **Render or Railway**: deploy `server.py` as a web service. Bind to `0.0.0.0:$PORT` (needs a small change: the host is currently pinned to `127.0.0.1`). Set `MCP_PATH_SECRET` and `MCP_PUBLIC_HOST` (the service hostname) as platform secrets. You get a stable HTTPS URL and no dependency on this PC. Free tiers may sleep, which adds cold-start latency to the agent's first tool call.
- Either way, the secret path is a temporary measure. Before write tools are exposed publicly, move to OAuth: the Cominty form supports an OAuth Client ID and Secret.


## 15. Run-scoped live findings (local implementation; not activated on the public server)

The current source uses schema v3. `Store.init()` migrates v1/v2 transactionally and
preserves existing policy/document versions, proposal content, review state, and events.
Historical event-based runs get stable Undrift IDs; old open v2 runs are interrupted
and their old tokens cannot be reused. Existing events are never rewritten. New proposals
persist their original quote and finding/run references directly in SQLite.

Do not run mixed old/new services against a database during rollout. Coordinate stopping
and restarting the MCP server and review API first. No service restart, remote agent update,
public finding enablement, migration of `data/undrift.db`, or paid run was performed as part
of local implementation verification.

- `UNDRIFT_MCP_FINDINGS=0` and `UNDRIFT_MCP_FINDINGS_PUBLIC=0` remain the defaults.
- Every run-scoped `get_policy`, `get_document`, and `record_finding` call must carry
  the run ID and secret run token. Unscoped reads remain available for preflight but
  do not verify retrieval. Tokens expire after 30 minutes and cannot extend their lifetime.
- The runner renews a 90-second lease every 20 seconds. API polling and startup reap
  expired runs or abandoned leases into explicit terminal states. A hard-killed runner
  can take up to 90 seconds plus the next poll to appear interrupted. Reads of an
  accepted document version and its finding commit independently of the final reply.
- Final JSON can fill missing findings, using the same checks. The exact document set,
  policy facts, versions, quotes and accepted finding fields must agree. Failed execution,
  UNKNOWN results, missing retrieval, invalid corrections or conflicts produce no proposals.
  All proposals in a run commit together with completed run state, or none do.
- Policy updates through the API require `expected_version` from the open editor.
- New corrections use the bounded sentence forms in `agents/patch.md`. Other prose is
  rejected for review rather than treated as safe. This is deliberately conservative,
  and supports the current English voluntary-return policy, not arbitrary policies.

### Offline verification

Use a fresh test directory name for concurrent backend test runs. Every database used here
is isolated; no command below invokes Cominty or touches the existing proposal.

```powershell
New-Item -ItemType Directory -Path .test-tmp -Force | Out-Null
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp=.test-tmp\backend-manual
# Keep the protocol harness's throwaway files inside the workspace:
$env:TEMP = "$PWD\.test-tmp"
$env:TMP = $env:TEMP
.\.venv\Scripts\python.exe scripts\test_mcp.py
.\.venv\Scripts\python.exe scripts\test_findings_mcp.py
$env:Path = "$PWD\.tools\node;$env:Path"
cd ui
npm test
npm run typecheck
npm run build
cd ..
```

The second protocol suite starts its own loopback server with ephemeral credentials,
`MCP_PUBLIC_HOST` empty, and public findings explicitly disabled. It verifies both MCP
protocol modes, scoped retrieval, retries, rejection, UNKNOWN, revocation and credential hygiene.

### First live Cominty finding test (only after implementation review and explicit go-ahead)

1. Manually copy the updated `agents/patch.md` to the existing Cominty agent's instructions.
   Do not change integration connections or broaden capabilities. This has not been done.
2. Create a dedicated demonstration database so the original pending proposal is preserved.
   In a new PowerShell terminal at the repository root:

   ```powershell
   $env:UNDRIFT_DB = "$PWD\data\record-finding-demo.db"
   .\.venv\Scripts\python.exe cli.py init
   .\.venv\Scripts\python.exe cli.py change-policy --shiftly-30
   ```

   Use a new filename if that demonstration database already contains proposals. The
   CLI reads the existing reviewer credential without printing it. Do not reject or
   apply `prop-04ca726838fc` to make room for this test.
3. Coordinate stopping the existing MCP process. Leave the tunnel and registered URL
   unchanged. In the same dedicated-database terminal, only after approval to expose
   authenticated finding submissions through that existing tunnel:

   ```powershell
   $env:UNDRIFT_MCP_WRITE_TOOLS = "0"
   $env:UNDRIFT_MCP_FINDINGS = "1"
   $env:UNDRIFT_MCP_FINDINGS_PUBLIC = "1"
   .\.venv\Scripts\python.exe server.py
   ```

   These are process-local overrides; do not edit `.env`. Keep the original public
   hostname, path-secret rotation settings and port. Server startup should report
   `record_finding: ENABLED on public host`.
4. In a second terminal set the same `UNDRIFT_DB` and run the free preflight:

   ```powershell
   $env:UNDRIFT_DB = "$PWD\data\record-finding-demo.db"
   .\.venv\Scripts\python.exe run_impact.py
   ```

   Require matching remote instructions, model and integration gates, and MCP endpoint
   `record_finding available; scoped_reads=True; shared_database=True`. This checks the
   configured endpoint through the tunnel and verifies the database identity, without starting an agent.
5. In a third terminal, serve this database on a separate review port so the original
   review service need not be restarted:

   ```powershell
   $env:UNDRIFT_DB = "$PWD\data\record-finding-demo.db"
   $env:UNDRIFT_API_PORT = "8101"
   .\.venv\Scripts\python.exe api.py
   ```

   Open `http://127.0.0.1:8101/#/impact` before the run.
6. After explicit approval to spend Cominty credit, invoke exactly once in terminal two:

   ```powershell
   .\.venv\Scripts\python.exe run_impact.py --run
   ```

   Expect four individual persisted findings to appear during analysis, one canonical
   `run-...` ID across views, and a completed run with one pending correction. No content
   should be approved, applied, or published. Inspect Activity and the saved
   `runs/impact_run-<id>.json` if the run is incomplete; do not blindly retry a paid call.
7. A rerun against a document with an open proposal is intentionally refused during
   finalization. Use a fresh isolated demo database or review the existing correction.
   Reverting to the original server/database later requires another coordinated switch;
   remove the process-local finding overrides when doing so.

`max_steps` and the local timeout do not provide a monetary spending cap. Remote cancellation
is best effort if the runner loses contact; inspect Cominty before authorizing a retry.


### Reliability verification before a live run

Findings appear immediately as provisional results. Only authenticated, determinate
findings from a completed and reconciled run establish consistency. A current completed
conclusion takes precedence over a provisional retry; retry evidence remains available
in the document inspector and run activity. Historical findings without authenticated
retrieval remain visible but do not establish verified consistency.

Corrections replace only recognized simple return-eligibility sentences. Mixed clauses,
conditions, refund-processing deadlines and unfamiliar phrasing fail closed for human
revision. This deliberately limits the current demo's supported wording.

Impact and run projections use a single SQLite read snapshot. The first terminal run
transition wins; subsequent cleanup cannot overwrite it. Runner evidence is checkpointed
atomically after acquiring the external handle and after each stream event. Failures
attempt remote cancellation when a handle exists and no terminal result was received.
Cancellation is bounded and best effort; a lost start response can still leave remote
work whose identity was never delivered locally.

The local vertical integration regression exercises MCP findings, final reconciliation,
API projection and the actual TypeScript consistency function using an isolated database:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_reliability.py -q -p no:cacheprovider --basetemp=.test-tmp\reliability-review
```

The UI calculation portion uses the portable Node 24 binary in `.tools/node`; it skips
that portion if the binary is unavailable. It does not contact Cominty or start an
application service. Full regression commands remain in the offline verification section.

## 16. Human-approved GitHub publishing and Shiftly

The standalone storefront is in `shiftly/`. Repository publishing is implemented
in `publishing.py` and the Review Queue, using a separate SQLite publication journal.
It is disabled until an operator configures the target and a private credential file.
It never merges pull requests or initiates Vercel deployments directly.

See [docs/PUBLISHING.md](docs/PUBLISHING.md) for isolated local verification,
preserving existing databases, account permissions, explicit repository/deployment
approval, and the complete human review → PR → merge → deployment → verification flow.
