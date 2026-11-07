# Native evidence and search certification

This revision fixes lifecycle coupling discovered by the first Reliability study.
It changes no agent instructions, corpus, rubric, factual answer key or search-order
criteria. It does not backfill or rescore any historical conversation.

## Observed v1 failure

The preserved run `run-91c9a70bdd8efc4a` emitted five genuine native questions.
Its first catalog call preceded its first retrieval of a search lead. The frozen
`search_first_v1` contract requires a retrieved lead before catalog reconciliation.
`clarification.capture()` previously called `validate_search_strategy()` before
inserting questions. The resulting exception rolled back all five questions, even
though `conversation_evidence` and the private trace contained the actual provider
message. The evaluator then detected a provider/persistence mismatch and stopped.
Its bare assertion produced an empty stop reason.

V1 remains the failed study, including its original product-level clarification
score and the separate diagnosis of successful provider question emission. Do not
run a rescore command over its report or repair its database. The frozen source is
available through its existing Git checkpoints, including `1436a4b`.

## Corrected transaction and continuation policy

`discovery.SearchStrategyViolation` distinguishes a failed quality contract from
authorization, version, context, storage or programming errors. The validator's
criteria and shared 12-search / 4-similarity limits are unchanged.

`clarification.capture()` first validates native prompts/options, current versions,
scope, authenticated retrieval and credential exclusion. It then assesses search
strategy independently:

| Result | Question persistence | Run / conversation | Continuation |
| --- | --- | --- | --- |
| Admissible questions, passing strategy | Exact prompts/options/context retained | incomplete / awaiting_answers | Authenticated answers, then separately authorized same-thread turn |
| Admissible questions, failed strategy | Exact prompts/options/context retained | failed / failed | Blocked; questions are audit evidence |
| Invalid context, scope, versions or credentials | No admitted question rows | Runner fails the phase | Blocked; raw redacted provider evidence remains |
| Persistence interruption | Transaction rolls back | Runner interruption/failure handling applies | Blocked; no blind redispatch |

For a strategy failure, question insertion, failed run/phase/conversation state,
token revocation and audit events commit atomically. Metadata records the observed
search checks and structured `strategy_validation` failure. The run receives no
finalization certificate, proposals or verified conclusions. The UI retains the
question context/options but disables answering and explains the blocked state.

A continuation cannot repair an earlier search-order violation. Both reviewer
answer submission and `prepare_continuation()` reject failed phases independently
of the conversation's display label. Reservation also requires actual saved questions
and all their answers. A corrupted or stale `ready` label cannot mint a new phase.
The read-only remote-thread preflight rejects blockers before querying the provider.
Ordinary successful clarification keeps its original same-thread behavior, fresh
credentials, re-retrieval requirement and immutable findings.

The runner reports native capture's actual state, preserves structured workflow
diagnostics, and tolerates heartbeat closure races for durably captured failed
question turns. A post-capture file failure cannot undo canonical SQLite evidence.
Unexpected errors are still failures and are never swallowed as strategy violations.

## Evaluation orchestration

The updated executor separates two outcomes in a **new, explicitly frozen series**:

- A search-strategy failure is a failed trial. Keep it in all result denominators,
  award no reconciled classification credit, preserve its genuine native question
  statistics, and skip fixture answers and continuation. The next independent slot
  may proceed only after all trust checks pass.
- Authentication/MCP errors, protected automation, changed seeded domain, unattributed
  scoped activity, provider/persistence mismatch, unexpected execution exceptions,
  interruptions, unknown cost and execution-budget failures stop the series.
  Global `health_check` calls are counted but are not scoped retrieval evidence.

This distinction does not relax the search or scoring criteria. A failed strategy
can never become reconciled by an incidental certificate or later conversation
label. Genuine native clarification recognition remains a separate metric from
classification accuracy, as specified by the original rubric.

Provider snapshots are written before consistency assertions, including observations
that cause a stop. Stop diagnostics always include an exception type and, where
available, a redacted reason, workflow code/details and source location. A local
execution exception is not ignored because the remote message later reports success.
The one-shot lock and dispatch markers remain immutable; no automatic retry is added.

`--series reliability-v2` selects separate `.tools/`, `data/` and `evaluations/`
paths. Report JSON still uses schema `reliability-v1`; **schema version and study
identity are different**. Protocol/plan record `series_id` and lifecycle revision
`native_evidence_independent_v2`. The executor refuses old lifecycle protocols.
Preparing a new study protects previous studies' databases, reports, private evidence
and dispatch markers recursively. Service activation derives both database and
report from the selected series, checks database identity and verifies the saved
process owner before replacing either of the two approved demo listeners.

## Proposed next execution (not performed by this fix)

First review/checkpoint the implementation. Obtain approval for the new series and
its service activation and paid-message allowance. The earlier failed paid message
remains a separate, reported cost; do not assume that starting a new version resets
authorization or a shared spending limit. Decide the new cumulative budget explicitly
before dispatch. The built-in fresh-series limit remains five conversations, one
continuation each, ten messages, and stop before a next message at five reported
platform units. A message may exceed that threshold while running.

From the repository root, after that approval:

```powershell
# Build into a new, unserved directory. The local fix already built the path below.
.\.tools\node\node.exe ui/node_modules/vite/bin/vite.js build ui --outDir ../.test-tmp/clarification-lifecycle-ui-v2 --logLevel warn

# Free: creates five fresh databases and privately precommits the unchanged facts.
.\.venv\Scripts\python.exe -B -m scripts.reliability_evaluation prepare --series reliability-v2 --ui-dir .test-tmp/clarification-lifecycle-ui-v2

# Service activation requires separate approval; only MCP 8001 and API 8401.
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/evaluation_services.ps1 -Config .tools/reliability-v2/active.json

# Free: exact remote instructions/model/allowlist, endpoint/schema/auth/DB identity,
# domain, fixture, source hashes and ngrok inspection checks. No agent execution.
.\.venv\Scripts\python.exe -B -m scripts.reliability_evaluation freeze --series reliability-v2

# Review and locally checkpoint the new frozen protocol before paid dispatch.
# Only after explicit approval for this NEW series:
.\.venv\Scripts\python.exe -B -m scripts.reliability_evaluation execute --series reliability-v2 --paid
```

Never reuse v1 databases, remove v1 locks, use `recover-preflight` after any paid
attempt, or combine v1 and v2 aggregate results. `recover-preflight` remains limited
to an entirely zero-dispatch infrastructure failure. A stopped new study also keeps
its unfavorable runs; a subsequent attempt requires another version and approval.

## Local regression coverage

`tests/test_clarification_lifecycle.py` covers all five real question bindings,
exact persistence and Review API visibility, failed-state authorization, revoked
credentials, strategy diagnostics, no proposals/classification credit, invalid
context rejection, rollback during storage failure and interruption, stale `ready`
labels, runner stream/post-capture failures, heartbeat closure races, provider-state
checks, separate series paths and a synthetic five-slot orchestration.

That orchestration exercises the actual dispatch guard, persistence, scorer and
report sinks with fake transport and no network: five quality failures remain five
failed measurements; persistence, interruption and provider mismatches stop after
one initial message; no failed conversation receives fixture answers or phase two.
Existing successful native same-thread continuation tests remain in
`tests/test_clarification.py`. Frontend regression tests retain failed question
evidence without offering a reviewer unlock or save action.

All of these are local regressions, not evidence of another successful live
Cominty execution. Running services still use their already-loaded implementation
until a separately approved activation.
