# Step 2: unified Shiftly discovery foundation

Native question capture and explicitly authorized same-thread continuation are
implemented locally; activation and a real development test are still required.
See [CLARIFICATION.md](CLARIFICATION.md) for lifecycle, limits and live-test steps.

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
For discovery runs, successful whole-batch reconciliation now records independent
document outcomes. UNKNOWN or failed document retrieval requires clarification for
that document; other authenticated, validated corrections may produce pending
proposals in the same atomic transaction. The run remains incomplete while any
document needs clarification, a correction or manual channel review. Only explicitly
certified document conclusions can count toward consistency in such a partial run.
Failed execution, stale versions, invalid evidence, missing scope or contradictions
still block the whole batch. Older non-discovery workflows retain their strict
whole-run retrieval/UNKNOWN gate. Do not classify uncertain text as certain to
obtain proposals. Legal/CRM channels cannot receive automated corrections even
when classified OUTDATED; this also gates approval/application of old proposals.

## Preparing the first live run (not performed)

`agents/discovery.md` is the instruction source to install through the existing
SDK's instruction-only agent update, after operator approval. Discovery mode in
`run_impact.py` no longer supplies a document list to the
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

The coordinated November 6 activation uses the existing ngrok hostname and
`undrift_mcp_ngrok`, MCP port 8001 and Review API/UI port 8401 against
`data/unified-demo-v1.db`. Archived Review services on 8101 and 8301 remain separate;
their databases and verified publishing evidence are unchanged. Process-local
overrides and verification evidence live in ignored `.tools/unified-*.json` files.
The full MCP URL contains a private path and must never be printed in instructions,
logs or reports. The remote instruction gate must match the current local file
before execution; a successful update never authorizes a paid run.

For the 24-document corpus, instructions cap `search_documents` at 12 calls and
`similar_documents` at 4, counting catalog pages, retries and calls delegated to
the integration subagent. Around 8-10 useful searches/catalog calls are a planning
target, not a required minimum. At least two search calls are reserved for catalog
coverage or its retry; catalog enumeration and count reconciliation protect recall.
The budget does not cap authenticated retrieval or individual finding submissions.
The local refinement additionally requires three distinct non-empty formulated
queries, including one without numbers, and retrieval of an available search lead
before catalog enumeration. New runs carry `discovery_strategy=search_first_v1`;
finalization checks actual audit events rather than an agent's claimed behavior.
Term-order variations do not count as distinct queries. Catalog completeness and
observed 12/4 budgets are checked; similarity remains optional. This is an
observable protocol check, not a test of the semantic quality of query choices.
Failed calls contribute to budgets when attributed by MCP instrumentation.
Query text is redacted/validated before bounded audit storage; full query hashes
remain available when stored text is truncated. Nothing regrades historical runs.

The runner command above keeps the existing explicit `--max-steps 40` limit;
the script's default remains 8. Parent execution steps and nested MCP calls are
different units: neither limit guarantees the other, completion or a monetary cap.
Search limits are instruction-level constraints with finalization-time audit checks,
not server-enforced request quotas or a monetary cap.
Review actual nested calls after the first run before designing repeated evaluations.

On November 6 the approved instruction-only SDK update completed and the unified
free preflight returned exit code 0. The normalized local and remote instruction
SHA-256 was `f5d9f0ee29a84ddd22b2f4299777afb7ce11259d45051a30e190c211c4498dcc`.
Only the agent's instructions changed; the Claude Sonnet 5.5 (High) model,
capabilities and `undrift_mcp_ngrok` allowlist and integration metadata were
unchanged. No paid execution occurred. The private operator report is
`.tools/discovery-instruction-verification.json`.

Confirm Cominty has rediscovered all seven tool schemas after the coordinated MCP
switch; a direct local preflight does not prove a cached integration manifest has
been refreshed. Both the SDK model and raw `GET /mcp/manifest` omit cached tool
definitions, so the successful free preflight cannot certify that cache. Inspect
the existing integration's tool list in Cominty; use its connection-test or
tool-refresh control if one is available, without creating another integration.
Expected tools are `health_check`, `get_policy`, `get_document`, `list_channels`,
`search_documents`, `similar_documents` and `record_finding`. If the UI offers no
such view/control, cache recognition remains unverified until an approved live
execution; do not claim that a manifest metadata check proves rediscovery.
Keep ground-truth test data outside all connected agent sources.

The first authorized discovery execution subsequently completed as
`run-317bb6de4608e935`: 24 MCP findings, 7/7 outdated precision/recall, 19/24
classification accuracy, and five UNKNOWN-to-UNRELATED errors. Only an empty
catalog search was used, with no similarity call. Its original database records,
seven pending proposals and private execution/evaluation artifacts are preserved.
See [the local refinement review](DISCOVERY_REFINEMENT.md). The revised local
instructions in this working tree have NOT been installed remotely. No service
restart or additional paid execution is authorized by this documentation update.

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
