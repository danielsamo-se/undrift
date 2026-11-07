# Reliability evaluation v1

This page records the original v1 study, which remains unchanged. For the lifecycle
fix and the required **new** series procedure, see
[Native evidence and search certification](CLARIFICATION_LIFECYCLE.md).
The current CLI requires an explicit `--series` argument; the historical v1 commands
below are not instructions to retry its paid execution.

Five independent deterministic Shiftly conversations, each with at most one native
question continuation. This development evaluation is separate from the earlier
discovery/clarification demonstrations. Their databases and traces are archives.
No agent tuning, remote instruction writes, replacement runs or automatic retries
are permitted after the protocol is frozen.

## Freeze and private facts

`scripts/reliability_evaluation.py prepare` creates five NEW schema-v5 databases
under `data/reliability-v1/`. Each has the same 24 documents, seven channels,
policy history and seed hash, but unique database and subsequent run identities.
It refuses existing paths. Historical workflows are not imported.

The private `.tools/reliability-v1/answers.v1.json` is created exclusively BEFORE
execution. Version `unavailable-facts-v1` supplies only "I cannot establish this
fact" for crm-002, bot-003, help-002, help-004 and support-002. It contains no
classification answers or invented purchase/promotion facts. Its SHA-256 is committed
in the frozen protocol, making a cryptographic precommitment without publishing
the private fixture. The evaluator submits the selected factual answers only after
matching real terminal native questions. Answer events identify
`evaluation:fixture-v1`, rather than claiming a person manually answered them.

`freeze` reads the existing Cominty agent/integration without changing them and
records normalized/raw instructions, model IDs/names, capability hash, integration
allowlist/hostname, tool schemas/annotations hash, corpus/seed/rubric/fixture hashes,
SDK version, scoring source hashes and execution limits in
`evaluations/reliability-v1-protocol.json`. Code/protocol receive a local checkpoint
before the paid batch. Labels, answer-book files and raw traces are not served by
MCP or the UI static directory. The runtime MCP scope contains only seeded documents.

## Paid execution and safety

The existing public endpoint and sole integration `undrift_mcp_ngrok` are reused.
Only the verified demo MCP/API listeners on 8001/8401 are switched between stores.
Ngrok, archive UIs, publishing and storefront services are not restarted.
Before each paid message the evaluator verifies the actual public MCP database ID,
tool schema hash and authentication boundary, API database, remote instruction/model/
capability configuration, source/rubric/fixture hashes, and preserved archives.
The current database is also checked for the identical seeded domain state.

Each conversation starts with 40 parent steps. A real native question turn may
receive the precommitted factual answers through the authenticated service layer,
then one `chat.send` on the SAME thread with 20 parent steps. Undrift's fresh-phase
credentials and retrieval/finalization requirements remain unchanged. No third
message is allowed by the batch, even though the general development conversation
model allows a larger lifetime. Search budgets remain shared across both phases:
12 search/catalog calls and four optional similarities. No search tuning is applied.

An exclusive batch lock prevents re-entry. Each POST is durably marked BEFORE
dispatch; its slot/phase is never retried. Provider failures, MCP/authentication
errors, schema/identity/configuration drift, missing native evidence or historical
data changes stop the batch. An unexpected question with no factual fixture also
stops execution; the evaluator never supplies a made-up answer.

The cumulative evaluation reported-cost gate is **5 platform units**. It is checked
before EVERY initial/continuation POST. Missing/failed cost evidence blocks further
paid work. A message started below five may exceed five while running; this is not
a hard within-message cap. Maximum authorization is five conversations/ten attempted
messages. The fixed prior development costs do not consume this evaluation-only gate.

No proposal approvals, applications, publication, GitHub writes or production resets
are performed. Valid finalization can create local pending-review proposals.

## Scoring contract

Primary classification metrics use the LAST phase only after successful exact
reconciliation (`completed` or the existing `per_document_v1` certificate).
That certificate also validates honest UNKNOWN labels despite excluding them from
the product's known-consistency credit. Failed or unreconciled phases receive no
primary classification predictions: missing labels count as misses. Their observed
provisional metrics remain visible separately. No earlier successful-looking phase
is substituted for a failed continuation.

- Outdated recall: true outdated detections / seven expected outdated documents.
- Outdated precision: true outdated detections / all predicted outdated documents.
- False positives: non-outdated documents classified outdated.
- Overall accuracy: exact categories / 24; missing labels are incorrect.
- Paraphrase detection: correct outdated labels for web-003 and bot-001 / two.
- UNKNOWN recall/precision: classification against five unresolved-context cases.
- Legal/CRM classification: exact categories across the six protected-channel docs.
  Automation safety separately checks that those channels received no corrections or
  proposals, and seeded domain/version hashes remain unchanged. CRM may legitimately
  be OUTDATED while remaining manual-only.
- Native clarification recall/precision: unique natively questioned document IDs
  against the five unavailable-context cases, independent of final classifications.
  Native prompts/options must match persisted terminal evidence and provider read-back.
- Retrieval: distinct authenticated document coverage, plus final-phase completeness.
- Search: actual queries/tool counts, audit of formulation-before-catalog/lead retrieval,
  coverage and shared budgets. Optional similarity is counted without a forced minimum.
- Cost and duration: actual SDK cost fields and persisted phase durations summed across
  both turns. API cost fields provide no currency label.
- Steps: reserved 40/20 parent allowances and unique `llm` event correlation IDs.
  The latter is a stream observation, not a provider-certified consumed-parent-step
  counter. Nested MCP calls are counted independently.

Minimum/mean/maximum include every recorded conversation, including failed and
business-incomplete runs. Undefined precision is `null`, with defined sample count;
it is never replaced with 100%. Not-started slots are explicitly counted rather
than fabricated as executions. If a stop prevents five runs, aggregates state the
actual denominator and remaining slots.

## UI and reproducibility

The existing React shell adds **Reliability** (`#/reliability`, shortcut 5).
`GET /api/reliability` reads only the sanitized report under `evaluations/`; it does
not return fixture files, credential values or raw provider traces. The page polls
every five seconds and shows aggregate ranges, per-run outcomes, genuine execution
labels, native question counts, identity/message references, tools, cost, timing,
provisional failures and explicit stop reasons. Impact/Review show the current
evaluation store; Reliability covers the whole batch.

```powershell
# First run isolated backend/UI/MCP checks and build into an unserved .test-tmp directory.
.\.venv\Scripts\python.exe -B -m scripts.reliability_evaluation prepare --ui-dir .test-tmp/reliability-ui-v1
# Activate only verified demo services against slot 1; requires authorized process access.
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/evaluation_services.ps1 -Config .tools/reliability-v1/active.json
.\.venv\Scripts\python.exe -B -m scripts.reliability_evaluation freeze
# Review/checkpoint protocol, implementation and tests locally before any paid request.
.\.venv\Scripts\python.exe -B -m scripts.reliability_evaluation execute --paid
```

The batch is intentionally one-shot. Do NOT rerun `execute` or delete its lock/attempt
markers after a stop. Read-only rescore/reproduction can use `reliability.score`
against each preserved database and the unchanged rubric; no agent call is needed.
`evaluations/reliability-v1-results.json` is the reproducible sanitized result artifact;
private plan/fixture/provider snapshots, attempt markers, completions and logs live
under `.tools/reliability-v1/`. Standard redacted agent traces remain in `runs/`.

The first infrastructure activation encountered a Windows inherited-pipe hang
BEFORE any conversation or paid dispatch. The launcher now uses a file sink and
a 30-second process timeout. `recover-preflight` is restricted to zero attempts,
zero runs and unchanged instructions/corpus/rubric/answers/provider configuration.
It archives the original protocol, stop report and lock, then records the source
correction as a pre-execution protocol revision. It cannot recover/retry a paid
execution. The original preflight checkpoint remains in Git history.

Regression tests cover failure denominators, undefined precision, native evidence
integrity, reconciled UNKNOWN handling, protected channels, corpus/rubric drift,
exclusive one-shot execution, the ten-message/cost gates, restricted report paths,
and UI failure/provisional/real-evidence presentation. All test providers are synthetic;
only the separately authorized guarded batch produces the real evaluation evidence.

## Actual v1 stop

The first paid conversation `run-91c9a70bdd8efc4a` was retained as a failure.
The agent performed four formulated queries, then catalog browsing (event 29),
then its first document retrieval (event 31). This violated the frozen requirement
to follow a search lead BEFORE catalog browsing. `clarification.capture` validates
that contract before committing native questions, so its transaction rejected the
otherwise genuine five native questions. Remote execution succeeded, local capture
failed, and the evaluator stopped on native evidence/persistence mismatch. No
continuation or conversations 2–5 were started. Cost: 0.4537737 reported units.

The report preserves primary missing-label penalties, 19 provisional observations,
full retrieval coverage and zero protected-channel edits. A separate, hash-bound
`reliability-v1-stop-diagnosis.json` records five genuine provider-emitted questions
and 100% provider emission recall, without changing the frozen product-persistence
scores. The Review API joins this diagnosis only when the frozen result hash matches.

This exposes a lifecycle coupling: search-strategy noncompliance currently prevents
persisting safe native questions. The generic capture exception/empty assertion
also obscures structured diagnostics in the run summary. A subsequent separately
reviewed change should consider retaining native questions while independently
recording strategy failure and should preserve structured failure details. No such
guard change, agent tuning, regrading, retry or further paid execution was made here.
The five-conversation study is incomplete; remaining authorization is not used to
bypass the required stop. Execution cannot be resumed by the preflight-only recovery.

Final local validation: **360 backend tests**, **61 React tests**, **65 MCP checks**,
**two storefront tests**, TypeScript and production build passed. Headless browser
verification confirms the Reliability page shows the failed real execution, four
unstarted slots, cost, aggregate ranges and provider-versus-persistence diagnosis.
Final API/UI is on `http://127.0.0.1:8401/#/reliability`, showing evaluation store 1.
Its final staged UI is `.test-tmp/reliability-ui-final`; no archived UI was rebuilt
or restarted. All historical file/domain checks and frozen source/configuration
checks passed. No proposal, approval, application, publication or GitHub write occurred.
