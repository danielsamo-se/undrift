# Native clarification and conversational continuation

Step 3 remains under development until a separately authorized real Cominty
question and follow-up have been observed. The local implementation uses installed
`cominty-sdk 1.2.0`: `Message.questions` (equivalent to `run.questions()`) and
`client.chat.send(existing_thread_id, message=..., agent_id=..., max_steps=...)`.
These are actual platform APIs. No invented pause/resume tool is exposed to MCP.
Remote instructions, services, historical runs and evaluation settings have not
been changed or frozen by this implementation.

## Lifecycle and records

`clarification_schema.py` adds four tables in schema v5, without importing history:

| Record | Responsibility |
| --- | --- |
| `conversations` | Root Undrift run ID, original Cominty thread, pinned agent/integration/instruction hash, bounded execution policy and state |
| `conversation_phases` | One new Undrift run and one external assistant message per turn, reserved steps, actual reported cost and terminal state |
| `clarification_questions` | Native prompt/options, local stable ID, authenticated document/policy version, retrieved context and immutable reviewer answer |
| `conversation_evidence` | Redacted stream, terminal and failure evidence persisted during execution independently of trace files |

`run_impact.paid_run` creates the conversation for a new discovery execution.
`_paid_run` starts the actual Managed Agent with the existing restricted thread
capabilities. It binds the returned message identity in SQLite before writing the
trace file, persists each stream event, and records actual `Result.data.cost`.
Server MCP events remain attributed to each phase's canonical run ID; provenance
includes conversation ID, ordinal, thread ID and message ID. Trace files are still
redacted, private and ignored.

After a successful terminal message, the runner inspects native questions BEFORE
JSON ingestion. Native prompts must use `[document:<id>;version:<version>]` and
refer to a policy/document actually retrieved through that phase's scoped MCP
capability. `clarification.capture` validates scope, versions, text/option bounds,
credential exclusion, then independently assesses the actual search-first audit.
Admissible questions are atomically retained even if that audit fails. A passing
audit closes the phase as incomplete; a failing audit closes the run and
conversation as failed and blocks answers/continuation. Both revoke the run-token
hash. See [lifecycle correction and new evaluation procedure](CLARIFICATION_LIFECYCLE.md).
No
lease or bearer credential remains live while waiting for a person. The terminal
turn is not called a completed analysis, and no proposal is created from it.
An initial native question requires real document context; failed retrieval still
uses honest UNKNOWN without an invented question binding.

The Impact Map shows native prompts, suggested radio options, document context,
free-text entry, phase status and reported costs. The existing reviewer unlock
flow keeps the token in memory only. `POST /api/questions/{id}/answer` authenticates
every answer, pins the context versions, validates the selected option or text,
and records an audit hash and reviewer identity. It does not start an agent or
approve a correction. Identical duplicate answers from the same reviewer are
idempotent; changed answers conflict. All questions must be answered before the
conversation becomes `ready`. UI says it is awaiting authorized paid continuation.

An explicitly authorized `--continue-conversation` execution first reads
`client.threads.get` and verifies the last native message and its question content.
An externally advanced/live/failed conversation fails closed. Then
`prepare_continuation` reserves exactly one phase under `BEGIN IMMEDIATE`, verifies
unchanged instructions, integration, model expectation, policy and every document
version, and mints a fresh run/token. It calls `chat.send` on the ORIGINAL Cominty
thread, with explicitly supplied step limit and the correlated factual answers.
The returned SDK follow-up has no `.thread`; the runner retains the original ID.
The SDK has no typed answer-ID API: locally stored question IDs and document/version
bindings accompany the answer text in an ordinary follow-up message.

Every document and policy must be retrieved again, with the fresh token, before
new findings/finalization. This deliberately costs more MCP calls than copying
old evidence. It keeps the existing run evidence rules and immutable findings
intact. Prior UNKNOWNs are never overwritten. Search audit is accumulated across
the linked phases, so continuation does not repeat discovery or reset the 12/4
budget. A final answer uses the existing exact full-scope JSON reconciliation and
atomic proposal guardrails. Honest residual UNKNOWN blocks only that document;
Legal/CRM remain classification-only even after clarification. Malformed/stale or
contradictory output blocks the batch. Known independent corrections can create
pending proposals only after successful reconciliation. They are held provisionally
during a native-question turn, rather than certified from an absent final JSON.

## Bounds and failure recovery

- At most **3 turns**, **40 parent steps per turn**, **80 reserved parent steps
  across the conversation**. For example 40 + 20 + 20. Reserved allowance is used
  because the SDK does not expose a reliable consumed-parent-step counter.
- The original 12 search/catalog and four optional similarity calls are shared
  across all phases and checked against actual server audit. These are nested
  MCP calls, separate from parent steps. As before, instruction/search-finalization
  budgets are not a network rate limiter.
- Cumulative **reported cost limit: 2 platform units**. Decimal costs are stored
  per message. Missing/failed cost reporting prevents continuation; reaching the
  limit prevents another turn. This is a between-turn gate, **not a hard monetary
  cap on an in-flight turn**: the SDK exposes no hard request spend limit or unit
  currency guarantee. Cost may exceed the limit within one allowed turn. Each
  turn still requires explicit operator authorization. No automatic paid retries.
- Question/option/text counts and aggregate answer size are bounded below the
  SDK's 30,000-character follow-up limit. A too-long factual answer is rejected
  before it becomes immutable.
- Ordinary runner leases/30-minute TTL apply per active phase. Waiting is a closed
  run, not a heartbeat loop. Expired/interrupted active phases are projected as
  expired/interrupted even after a process crash; they cannot be blindly resent.
- On interruption, partial SQLite/trace evidence and any reported cost remain;
  cancellation is attempted when a message handle exists. A failed/ambiguous POST
  without a returned handle is marked dispatch-uncertain in evidence. A reserved
  phase is not automatically retried. An operator must inspect the remote thread
  and message via read-only SDK metadata before deciding how to recover. This
  version deliberately has no automatic adopt/replay/recovery CLI.
- There is a small unavoidable race between the remote read check and POST if
  someone else uses the same thread. Do not interact with an active evaluation
  thread in Cominty. The SDK has no conditional-send or idempotency-key API.
- Factual reviewer input is trusted as human-provided context, not independently
  verified truth. It cannot bypass channel/content guards or approve publication.
  The model's reasoning about those facts still requires live evaluation.

## Evaluation adapter

`scripts/evaluate_clarification.py` operates only on an explicit prepared unified
database. It does not initialize/reset a database or execute an agent. It reads
captured native terminal evidence and questions, rejects mismatched/missing native
evidence, and can submit predefined factual answers through the same authenticated
service layer used by the API. Keep its answer book under ignored `.tools/` or
another private operator location. It is not imported by MCP, runner or API, and
neither labels nor the complete answer book are supplied to the agent. Only the
selected factual answer enters the continuation, as intended.

Private book format (keys are the actually retrieved document IDs):

```json
{"<document-id>": {"text": "<predefined factual context>"}}
```

Alternatively use `{"option": "<exact offered label>"}`. Text answers are more
stable for frozen evaluation because agent-generated option wording can vary.
Establish the facts and treatment of unavailable facts BEFORE evaluation; do not
change answers after observing the classification. Preserve pre-answer UNKNOWN
question recall/precision separately from post-answer classifications. Do not
silently rescore a context-free UNKNOWN ground truth as a resolved label.

```powershell
.\.venv\Scripts\python.exe -B -m scripts.evaluate_clarification --db <new-db> --conversation <root-run-id> --ground-truth tests/data/discovery_ground_truth.json
# Explicit LOCAL answer submission only; no agent execution:
.\.venv\Scripts\python.exe -B -m scripts.evaluate_clarification --db <new-db> --conversation <root-run-id> --answer-book .tools/<private-book>.json --submit-answers
```

## One controlled live test, AFTER separate authorization

1. Preserve the first-run unified store and all archives. Create a NEW deterministic
   seed, e.g. `python scripts/prepare_unified_demo.py --database data/clarification-development-v1.db`.
   Do not copy/rewrite historical findings into it. Schema v5 is applied only there.
2. With separately approved service activation, point the demo MCP/API process-local
   `UNDRIFT_DB` overrides at that SAME new file, use the current ngrok hostname,
   port 8001, existing secret path and sole `undrift_mcp_ngrok` integration. Serve
   the staged UI build on 8401; do not restart archive/publishing services. Seven
   tools remain unchanged; no answer, approval or publishing MCP tool is added.
3. Separately authorize updating ONLY the remote instructions to local
   `agents/discovery.md`. Preserve model Claude Sonnet 5.5 High and the allowlist.
   Run `python run_impact.py --discovery --max-steps 40` with the existing private
   process-local environment/hostname/DB overrides. This is the free no-agent
   preflight; verify instruction hash, model/allowlist, database ID and scoped tools.
4. Authorize ONE initial paid turn, then run
   `python run_impact.py --run --discovery --max-steps 40` in that same private
   environment. Exit **3** means genuine native questions await answers. Exit 1
   means incomplete/failed, not permission to retry. Save actual thread/message,
   server MCP calls, native questions, duration and reported cost. If Cominty only
   emits prose, do not fabricate native events or claim this milestone succeeded.
5. Open `http://127.0.0.1:8401/#/impact`, unlock the reviewer session through the
   existing private-token flow, select options or give factual context, and save
   each answer. Check `/api/conversations` reports `ready`. No payment starts here.
6. Authorize exactly ONE paid follow-up, then run
   `python run_impact.py --run --discovery --continue-conversation <root-run-id> --max-steps 20`
   using the SAME environment/database/connection. Verify `chat.send` retains the
   original thread, creates a new external message and fresh local phase, old
   tokens fail, new authenticated retrieval/findings occur, and final JSON agrees
   with the new persisted findings. No approvals/publishing/reset/auto-retries.
7. Evaluate native question recognition and final classifications separately;
   preserve evidence and inspect protected channels. Only AFTER this development
   test should instructions, factual answer protocol, model, seed, budgets and
   evaluation methodology be frozen for five independent conversations.

The production website already shows 30 days. This development test exercises
clarification and pending proposals only. It must not reset production or attempt
publishing against the existing corrected repository.

The runner commands above require these PROCESS-LOCAL overrides in the operator
terminal after the new database and matching services have been separately approved
and prepared. These do not modify `.env`. Credentials are loaded privately from
the existing configuration; never paste them into a command or chat:

```powershell
$env:UNDRIFT_DB = (Resolve-Path data/clarification-development-v1.db).Path
$env:MCP_PORT = '8001'
$env:MCP_PUBLIC_HOST = 'chatter-poker-theorize.ngrok-free.dev'
$env:UNDRIFT_MCP_CONNECTION = 'undrift_mcp_ngrok'
$env:UNDRIFT_EXPECTED_MODEL = 'Claude Sonnet 5.5 (High)'
```

## Local verification

`tests/test_clarification.py` covers native model/runner capture, context and secret
validation, option/free-text reviewer auth, duplicate/concurrent submissions,
expired credentials, stale context, pinned configuration/budgets, actual same-thread
send contract, cancellation/partial cost evidence, fresh evidence requirements,
immutable earlier findings, exact final reconciliation, protected-channel isolation,
evaluation provenance and additive v4 migration. UI tests cover prompts, context,
unlock, options/free text, errors and explicit paid-execution boundary. Provider
execution is mocked in tests; no claim of live native emission is made.

Reproduce with the isolated commands in `docs/DISCOVERY_REFINEMENT.md`; use a fresh
`.test-tmp/` basetemp/build directory each time. Do not overwrite `ui/dist` or another
directory currently served by a demo. No paid service is needed for these tests.

Final local verification: **341 backend tests** (40 new clarification regressions),
**58 UI tests**, **65 MCP checks** (30 base, 25 findings, 10 discovery), **two
storefront tests**, TypeScript checking and production UI build passed. The build
is staged at `.test-tmp/clarification-ui-final-6c5e2144cff54df8b4275a1abcc8f88b`;
it has not replaced a served directory. Preservation checks found all 18 protected
database/evidence/seed/configuration files byte-identical. No paid request, remote
change, service restart, proposal approval or publication was performed.
