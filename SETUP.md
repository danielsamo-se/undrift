# Undrift: setup

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

## 2. Local tests (30/30 protocol checks, 80 pytest tests, 26 UI tests)

```powershell
.\.venv\Scripts\python.exe scripts\test_mcp.py
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
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

Write tool: `submit_proposal` exists only with `UNDRIFT_MCP_WRITE_TOOLS=1`, and `server.py` refuses to start that way while `MCP_PUBLIC_HOST` is set. It can only create `pending_review` proposals.

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
