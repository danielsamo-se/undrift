# Undrift preflight: setup

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
`--rotate` replaces the secret. After rotating, update the URL in Cominty (step 5).

## 2. Local test (done, 27/27 PASS)

```powershell
.\.venv\Scripts\python.exe scripts\test_mcp.py
```

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

## Later: a stable URL (not configured; ask first)

- **Named Cloudflare Tunnel** with a custom domain: needs a Cloudflare account and a domain on Cloudflare. `cloudflared tunnel login`, `cloudflared tunnel create undrift`, then a DNS route such as `mcp.<yourdomain>` and a `config.yml` ingress to `http://127.0.0.1:8000`. The hostname stays stable across restarts, so the Cominty URL never changes. The server still runs on this PC.
- **Render or Railway**: deploy `server.py` as a web service. Bind to `0.0.0.0:$PORT` (needs a small change: the host is currently pinned to `127.0.0.1`). Set `MCP_PATH_SECRET` and `MCP_PUBLIC_HOST` (the service hostname) as platform secrets. You get a stable HTTPS URL and no dependency on this PC. Free tiers may sleep, which adds cold-start latency to the agent's first tool call.
- Either way, the secret path is a temporary measure. Before any write tools exist, move to OAuth: the Cominty form supports an OAuth Client ID and Secret.
