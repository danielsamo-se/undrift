# Step 2: unified Shiftly discovery foundation

Step 1 checkpoint: `59c34aa` contains the live MCP findings workflow and reviewed
GitHub/production verification implementation. Historical databases, journals,
credentials, runs/ traces and private operator tools remain ignored archives.
This step imports none of those records. It makes no remote setting or service changes.

## Fresh deterministic state

From the repository root:

```powershell
.\.venv\Scripts\python.exe scripts\prepare_unified_demo.py --database data\unified-demo-v1.db
```

The destination must not exist. The command never resets or copies a database.
The seed has policy v1 (60 days), policy v2 (30 days from November 4, 2026), 24
versioned documents, seven channels, one explicit fixture event and no agent runs,
findings, proposals, reviewer decisions or publication jobs. Document versions start
at 1. The channel sizes are Website 4, Help Center 4, Order emails 3, Support
snippets 4, Bot knowledge 3, Legal 2 and CRM 4. The corpus includes numerical and
two-month return claims, independent legal rights, earlier commitments, ambiguous
scope and unrelated uses of 60 days.

`discovery_seed.manifest()` and the stored seed SHA-256 describe identical logical
starting states. Each new copy gets its own database identity so independent runs
and publication authorizations cannot collide. SQLite file bytes and that identity
are intentionally not the reproducibility metric. All fixture timestamps, domain
IDs, versions and text are deterministic. No seeded record claims to be historical
Cominty execution or human approval. Ground truth is a test-only artifact; neither
the seed DB nor MCP tools or agent instructions expose it.

Schema v4 adds a current-version FTS5 index, maintenance triggers and optional
finding corrections. Existing migrations remain transactional/additive. New code
can read archived v3 findings without an implicit migration. No original store has
been migrated by this work.

## One store for the product workflow

The unified profile explicitly permits `publications` and `publication_events` in
the same SQLite file as policies, document histories, authenticated retrievals,
runs, findings, proposals, reviewer decisions and activity events. The existing
Publisher, approval digest, GitHub adapter and production verification are reused.
For ordinary/archived stores the separate-journal requirement remains unchanged.
When configuring this profile, leave `UNDRIFT_PUBLISH_DB` unset or set it to the
unified database. No credentials belong in the database or storefront.

The default publishing target remains `doc-001` -> `content/returns.txt` in a
separately configured storefront repository. Other channels can produce reviewed
local proposals; this step does not add external publishing adapters for them.

## MCP boundary

Discovery is opt-in (`UNDRIFT_MCP_DISCOVERY=1`), preserving the old three/four-tool
interfaces by default. The new tools require a trusted local run's credentials:

- `list_channels`: only authorized channels and scoped counts.
- `search_documents`: literal-word FTS5 search over titles/content, or empty-query
  catalog browsing with offset pagination and a maximum page size of 25.
- `similar_documents`: deterministic classical TF-IDF cosine scores calculated
  from the authorized, version-pinned corpus. No embeddings or AI model downloads.

Queries are bounded and parameterized. FTS operators are treated as literal words.
Similarity and search are relevance aids, not conclusions. Their results never
satisfy `run_retrievals`: `get_policy` and `get_document` must actually return the
run-pinned versions before a finding can cite evidence. The unified profile rejects
unscoped reads and omits the document ID catalog from unauthenticated health output.
Successful discovery and MCP calls carry the canonical Undrift run ID in the audit
log. Invalid credentials never acquire a caller-supplied run attribution.

`record_finding` accepts optional `proposed_content` for OUTDATED only. The exact
quote, category, versions, reason and correction are validated. Corrections use the
existing preservation/effective-date/protected-rights guardrails. Accepted findings
and corrections are immutable; identical concurrent retries deduplicate. No MCP
tool creates proposals, renews leases, approves, applies, publishes or mints tokens.
The trusted runner reconciles final JSON and atomically creates pending proposals.
Discovery findings may omit a correction; final JSON may reuse an accepted one.
UNKNOWN findings or incomplete authenticated retrieval still block all proposals
for that run, as in the existing safety policy. The ambiguous full corpus can thus
finish as incomplete while retaining all its findings. A complete, explicitly
bounded actionable scope can demonstrate proposal creation without relaxing this
rule. Do not classify uncertain text as certain just to obtain proposals.

## Preparing the first live run (not performed)

`agents/discovery.md` is a local instruction candidate. It has NOT been installed
remotely. `run_impact.py --discovery` no longer supplies a document list to the
agent: the trusted scope pins versions internally while the agent discovers IDs.
It checks this profile, scoped discovery schemas, shared endpoint DB, model and
remote instruction match before any spending. It requires an explicitly selected
isolated database and refuses the original `data/undrift.db`.

After separate approval to start/repoint services and update the remote agent:

```powershell
$env:UNDRIFT_DB = "$PWD\data\unified-demo-v1.db"
$env:MCP_PUBLIC_HOST = '<approved public hostname>'
$env:MCP_PORT = '<coordinated MCP port>'
$env:UNDRIFT_MCP_CONNECTION = 'undrift_mcp_ngrok'
# Server process settings, only after public findings approval for this database:
$env:UNDRIFT_MCP_DISCOVERY = '1'
$env:UNDRIFT_MCP_WRITE_TOOLS = '0'
$env:UNDRIFT_MCP_FINDINGS = '1'
$env:UNDRIFT_MCP_FINDINGS_PUBLIC = '1'
# Start/repoint MCP and the review API only as a coordinated next operation.
# Then run free checks first; --run remains a separately authorized paid action:
.\.venv\Scripts\python.exe run_impact.py --discovery --max-steps 40
```

The current ngrok target and running services still use the archived demos; no
unified database endpoint is active yet. The existing remote instructions describe
the older workflow, so the discovery instruction gate should fail until explicitly
updated and reverified. Twenty-four documents need a deliberate budget; eight old
default tool rounds are not an adequate experiment design.
Confirm Cominty has rediscovered all seven tool schemas after the coordinated MCP
switch; a direct local preflight does not prove a cached integration manifest has
been refreshed. Keep ground-truth test data outside all connected agent sources.

## Production and evaluation blockers

Production Shiftly already serves the corrected 30-day content. The fresh seed's
60-day FAQ intentionally matches the original scenario, so publishing preview must
reject the current production repository as stale. A later, explicitly authorized
reset must restore the 60-day source through a traceable Git commit and deployment.
This work does not reset production, open PRs, change GitHub/Vercel or copy historical
verified state into the new store.

Step 3 evaluation is not implemented or run here. It should create five independent
fresh seeded stores, freeze identical instruction bytes and model configuration,
and measure variation rather than demand identical agent outputs. Capture the seed
hash, instruction hash, expected/observed model, each independent run/thread/message
ID, retrieval coverage, finding accuracy, corrections, costs and failures. The
discovery runner already records the seed/instruction hashes and expected model;
its ordinary redacted execution evidence records actual model events. No five-run
loop or automatic retries are enabled by this step. Guard, Support Bot, additional
channel adapters, versioned agent Memory and public deployment remain later work.

## Isolated verification

```powershell
$scratch = '.test-tmp\discovery-tests-' + [guid]::NewGuid().ToString('N')
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=$scratch
.\.venv\Scripts\python.exe scripts\test_mcp.py
.\.venv\Scripts\python.exe scripts\test_findings_mcp.py
.\.venv\Scripts\python.exe scripts\test_discovery_mcp.py
```

Tests exercise scoped catalog/search/similarity, current-index updates, stale or
cross-run credentials, exact evidence, immutable corrections, UNKNOWN handling,
protected categories, concurrent deduplication, MCP serialization, UI projections,
proposal finalization and the real Publisher/API against simulated GitHub/production
transports in the unified file. They never access live databases or paid services.
