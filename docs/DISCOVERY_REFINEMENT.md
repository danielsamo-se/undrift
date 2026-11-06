# Local refinement after the first discovery execution

This review preserves the first execution and evaluation; it does not reclassify
stored findings, edit proposals, change labels or reset any seed/live store.
The instruction candidate and service changes below need a separately authorized
development run. No model, remote instructions or production service was changed.

## Classification rubric and the five errors

The existing ground truth is a label mapping in
`tests/data/discovery_ground_truth.json`, not an independently detailed prose rubric.
The original instructions said UNKNOWN for unclear scope but described UNRELATED
too broadly as unaffected content. The model repeatedly used "no contradiction"
or "states no period" as a reason to label unresolved return advice UNRELATED.
The revised instructions distinguish subject independence from unresolved scope:

| Document | Missing fact / reason UNKNOWN remains correct | First-run reasoning |
| --- | --- | --- |
| help-002 | Original order offer and the window it promised | No period asserted; defers to confirmation |
| help-004 | Original terms determining eligibility; processing duration does not resolve them | 60 days is processing, not entitlement |
| support-002 | Purchase date and which window actually applies | Conditional guidance, no general period |
| bot-003 | Promotion eligibility and the actual promotion terms | Hedged pointer, no contradiction |
| crm-002 | Purchase date and the corresponding original promise | Pending check, no period stated |

UNKNOWN can follow successful retrieval: retrieving conditional advice does not
retrieve the missing facts it points to. The reason should identify that fact and
a clarification question. UNRELATED applies to an independent subject such as
shipping charges, retention or testing. A processing-only statement can be
unrelated; a mixed document with unresolved eligibility cannot be classified from
its harmless processing number alone. No document ID, expected category count or
ground-truth mapping is included in agent instructions or runtime sources.

Local tests verify correct product handling of labelled UNKNOWN/UNRELATED inputs.
They cannot establish that the remote model will make better decisions; the next
approved development run must measure that without changing the expected labels.

## Search strategy

The previous budget gave maxima and catalog completeness, with no required
formulated search stage. The agent found everything with one catalog call, so
there was no incentive to explore terminology. Revised instructions require:

1. Retrieve the authoritative policy and discover channel counts.
2. Make at least three distinct meaningful non-empty searches, covering policy
   terminology, a number-free paraphrase and conditional/earlier eligibility.
3. Retrieve relevant leads before catalog browsing, and refine searches when
   retrieved wording provides useful new leads. Similarity is optional.
4. Reconcile the remaining catalog, retrieve all documents and submit findings.

The existing maximum of 12 search/catalog calls and four similarity calls remains;
retries and delegated calls share the budgets, with two calls reserved for catalog
coverage/retry. No 8-10-call minimum or compulsory similarity call wastes budget.
Forty parent execution steps remain distinct from nested MCP calls and cost.

`run_impact.paid_run` tags future discovery runs with `search_first_v1`.
`discovery.validate_search_strategy` verifies successful distinct term sets, a
number-free formulation, available lead retrieval before the first catalog call,
complete catalog coverage and observed budgets using server audit events. Three
equivalent term reorderings do not qualify. If searches provide no leads, retrieval
can proceed from the catalog. This gate validates observable behavior, not whether
queries were insightful. Direct tool calls are not hard rate-limited by this gate.
Search text is credential-checked before bounded audit persistence alongside its
full SHA-256. Existing events and run contracts are unchanged.

## Seven-proposal audit

All seven original proposals remain pending exactly as recorded. Their corrections
use the November 4 effective date and preserve earlier purchase promises.

| Document | Content audit | New automation eligibility |
| --- | --- | --- |
| bot-001 | Replaces two-month eligibility; retains grandfathering | Eligible for human-reviewed proposal |
| crm-004 | Preserves the separate FAQ-advice sentence | **Manual-only CRM channel; automation blocked** |
| doc-001 | Replaces general 60-day eligibility with scoped 30-day wording | Eligible |
| help-001 | Preserves order-number reminder verbatim | Eligible |
| mail-002 | Preserves subject-line sentence verbatim | Eligible |
| support-001 | Preserves receipt condition verbatim | Eligible |
| web-003 | Replaces two-month eligibility with scoped wording | Eligible |

Content guardrails alone allowed the CRM proposal. `workflow.MANUAL_ONLY_CHANNELS`
now protects `legal`, `crm`, `legal-terms` and `support-crm` regardless of the text,
classification or actor string. MCP corrections and final fallback corrections are
rejected; direct proposal creation, approval/application and Publisher eligibility
are checked again. Rejecting a historical protected proposal remains permitted.
There is no manual editing override or new clarification-resolution API in this
round. New channel IDs must be explicitly reviewed for inclusion in this registry.
The next correctly labelled full-corpus run may create six safe proposals while
requiring five clarifications and separate handling of the outdated CRM advice.

## Per-document finalization and consistency

Discovery finalization still requires a successful execution, exact final scope,
current versions, exact evidence and reconciliation with every immutable MCP
finding. Any malformed/contradictory entry, unsafe patch or stale document rolls
back the entire proposal/certification transaction. A valid UNKNOWN is not such
a protocol violation.

After global reconciliation each document receives an outcome in trusted run
metadata: `proposed`, `no_change`, `needs_clarification`, `manual_only` or
`needs_correction`. A known finding requires both authenticated policy and document
retrieval. UNKNOWN receives no proposal and no consistency credit; a failed policy
retrieval cannot authorize any correction. A failed retrieval of a different
document does not invalidate an independently verified correction.

An incomplete run receives `finalization=per_document_v1` and an explicit immutable
set of `verified_document_ids` only when the reconciliation transaction succeeds.
`workflow.verified_finding` uses that certificate for projections. Ordinary failed,
interrupted, expired, running or rejected/incomplete runs receive no exemption.
UI details show missing-context clarification or protected-channel manual review;
known independent conclusions retain their classifications without crediting the
unresolved documents. Terminal partial runs still return a needs-attention exit
code; that must never trigger an automatic paid retry.

Original completed runs retain their original projections. In particular, the
first run's five incorrect UNRELATED classifications are not silently repaired.
To resolve a clarification later, establish authoritative contextual facts and
perform a separately authorized new analysis; never mutate the old finding.

## Next controlled development run

After review, separately authorize loading the new MCP/API code and staged UI,
updating the remote instructions to the local candidate, and a development run
against a newly seeded isolated store. Preserve this first-run store and artifacts.
Check code/version compatibility as well as the exact instruction hash: the old
running MCP process lacks the new query audit fields needed by `search_first_v1`.
Health now advertises the supported discovery strategy, and the discovery free
preflight refuses an older server that does not advertise `search_first_v1`.
No migrations are required. The future five evaluation runs must then freeze one
seed hash, instruction hash, strategy version and model configuration consistently;
do not mix this initial exploratory run with those independent starting states.

## Local verification

Final isolated results: 301 backend tests, 53 UI tests, 30 legacy/base MCP checks,
25 finding MCP checks and 10 discovery MCP checks passed. TypeScript checking and
a production UI build passed. The UI build was staged under a fresh `.test-tmp/`
directory, without overwriting any directory served by the running demos.

`tests/test_discovery_improvements.py` covers all five contextual UNKNOWNs and the
independent UNRELATED documents as labelled input contracts; full-corpus proposal
isolation; honest failed retrieval; failed execution; stale/conflicting replies;
concurrent finalization; protected-channel bypasses through correction, proposal,
approval, apply and publishing paths; actual search audit behavior; and an
MCP-to-finalization-to-API integration. It also checks that subsequent failed,
running or unreconciled findings cannot override a certified partial conclusion.
`tests/test_runner.py` covers the partial-finalization/heartbeat race. UI tests
cover clarification messages, manual-only notices and partial consistency counts.
No test substitutes a scripted classifier for evidence of model improvement.

Reproduce the isolated checks from the repository root:

```powershell
$scratch = '.test-tmp/refinement-' + [guid]::NewGuid().ToString('N')
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider --basetemp=$scratch
.\.venv\Scripts\python.exe -B scripts/test_mcp.py
.\.venv\Scripts\python.exe -B scripts/test_findings_mcp.py
.\.venv\Scripts\python.exe -B scripts/test_discovery_mcp.py
Push-Location ui
..\.tools\node\node.exe node_modules/vitest/vitest.mjs run
..\.tools\node\node.exe node_modules/typescript/bin/tsc --noEmit
$build = '../.test-tmp/refinement-ui-' + [guid]::NewGuid().ToString('N')
..\.tools\node\node.exe node_modules/vite/bin/vite.js build --outDir $build
Pop-Location
```

Private `.tools/discovery-improvement-proposal-audit.json` records the read-only
seven-proposal audit. Preservation checks cover 18 existing files, including the
demo databases/WALs, first-run execution/evaluation artifacts, seed, labels and
`.env`; all remain byte-identical. The credential file was not written. No
Cominty call, remote update, service restart, approval or publication was performed
in this local improvement round. No changes were committed.
