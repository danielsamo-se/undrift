# Live Cominty demonstrations (7 November 2026)

Sanitized evidence: `evaluations/live-demos-2026-11-07.json`. Each demonstration was one explicitly authorized paid execution with no retry. Both are now included in the publicly verified v4 jury replay. They remain historical, read-only evidence; publishing the replay did not run either agent again.

## 1. Real Guard BLOCK

| | |
|---|---|
| Candidate | "For orders placed on or after November 4, 2026, customers have eight weeks to return their purchases." |
| Why it matters | Eight weeks is 56 days, but policy v2 sets 30 days. The deterministic precheck only reads numeric day periods, so it returned **CLEAR**. |
| Result | **BLOCK** from `cominty_guard`, with assessment `CONTRADICTS_POLICY` citing `P`. Server verification passed with no problems. Not release-eligible. |
| Check | `gchk-5ce8448d76f85805` in the local Guard demo (`guard-demo-v1`, :8503) |
| Execution | Guard agent `_ofnqauQg4_y`, Sonnet 5.5, 5.7 s, no tool calls, cost 0.0282 |

This is the first Guard verdict that only the agent could reach: no deterministic rule fired. The service was put back in non-paid mode right after the check.

At the time of this check, the precheck did not recognise spelled-out week or month durations. A wrong ALLOW on this candidate would therefore have been release-eligible.

That gap is now closed in `d3e24e1`. Periods in weeks, months or years escalate:
- to NEEDS_REVIEW (`period_not_in_days`);
- to BLOCK when they equal a superseded period, for example "two months".

The rule can only escalate. The same candidate would now get precheck NEEDS_REVIEW, so a wrong ALLOW can never release it, while a correct BLOCK still comes from `cominty_guard`. The recorded check keeps its original precheck result (CLEAR), which is what the UI shows.

## 2. Real Help Center autonomy attempt

**Scenario.** `scripts/create_autonomy_live_demo.py` built `data/autonomy-live-v1.db`, scenario `autonomy-live-v1`. It is a new three-document corpus, separate from the 24-document scenario:
- `help-101`: "Items can be returned within 60 days. Keep your receipt handy."
- `help-102`: the statutory 14-day right.
- `web-101`: shipping.

Every fact these documents need is in the policy. The reviewer delegated Help Center (`bounded_auto`, at most one application per run, version 1) before the run, and the run pinned that grant.

**Run.** `run-84e73977cab0191d` used the unchanged Impact Agent (instructions `ed03b7f2…`) through the real authenticated MCP endpoint. It took 66 s and cost 0.148.

**Result: failed closed.**
- **Classifications were correct:** OUTDATED, VALID_DISTINCT and UNRELATED, all recorded via MCP after authenticated retrieval.
- **Search contract failed:** the agent ran three formulated searches and the catalog browse before retrieving any search lead (`lead_followed=false`). The server therefore rejected the reply under the search-first contract. This is the same failure as the two failed reliability-v2 trials.
- **Nothing changed:** no proposal and no application; every document stayed at version 1.

**Why a real application is not achievable here without changing the frozen instructions.**
- **The agent's correction shape:** in this run, and in all three successful reliability-v2 trials, the agent's correction added "Purchases made before 2026-11-04 keep the return period promised at the time of purchase."
- **What the finalizer accepts:** it auto-applies only a single-sentence replacement with no protected (commitment or statutory) sentence. For such an edit it returns `not_single_sentence_correction` and `policy_sensitive_exception`, and the proposal waits for a human.
- **Local simulation confirms it** (scripted, not Cominty evidence): a one-sentence correction is applied with a receipt, while the agent's shape is refused.

**Conclusion.** The application story stays with the labelled local fixture. The real run demonstrates delegation, pinning and fail-closed contract enforcement.

## Preservation

Logical table digests of nine protected databases were compared before and after.
- **One change:** restoring the reliability-v2 loopback MCP service appended a single `server.started` system event (seq 228) to `evaluation-05.db`, as every earlier restart of that service did. Audit rows are append-only, so it was left in place.
- **Unchanged:** no trial run, finding, proposal or result row; the Impact and Guard agent configurations; the jury site; Shiftly production; Slack.
