# Undrift Reliability Evaluation v1

State: **stopped**. Recorded conversations: **1/5**. Reconciled: **0**. Attempted paid messages: **1/10**.

Frozen protocol SHA-256: `de9212c346e1008698a53995741df11d289107a9e4ecf99f491fac68b925a336`.

Five independently seeded stores use the same frozen instructions, model, tool configuration and rubric. Native question answers come from the precommitted private unavailable-facts fixture. The earlier development runs are excluded. No unfavorable result is replaced.

| Metric | Minimum | Mean | Maximum | Defined / recorded |
| --- | ---: | ---: | ---: | ---: |
| accuracy | 0.00% | 0.00% | 0.00% | 1/1 |
| false_positives | 0.00 | 0.00 | 0.00 | 1/1 |
| native_clarification_precision | N/A | N/A | N/A | 0/1 |
| native_clarification_recall | 0.00% | 0.00% | 0.00% | 1/1 |
| outdated_misses | 7.00 | 7.00 | 7.00 | 1/1 |
| outdated_precision | N/A | N/A | N/A | 0/1 |
| outdated_recall | 0.00% | 0.00% | 0.00% | 1/1 |
| paraphrase_recall | 0.00% | 0.00% | 0.00% | 1/1 |
| protected_classification_accuracy | 0.00% | 0.00% | 0.00% | 1/1 |
| retrieval_coverage | 100.00% | 100.00% | 100.00% | 1/1 |
| unknown_precision | N/A | N/A | N/A | 0/1 |
| unknown_recall | 0.00% | 0.00% | 0.00% | 1/1 |
| duration_s | 170.76 | 170.76 | 170.76 | 1/1 |
| reserved_parent_steps | 40.00 | 40.00 | 40.00 | 1/1 |
| observed_llm_rounds | 6.00 | 6.00 | 6.00 | 1/1 |
| native_question_count | 0.00 | 0.00 | 0.00 | 1/1 |
| mcp_error_count | 0.00 | 0.00 | 0.00 | 1/1 |

| Conversation | Root run | Final state | Reconciled | Accuracy | Outdated recall / precision | UNKNOWN recall | Native clarification recall | Messages | Reported cost | Duration |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | `run-91c9a70bdd8efc4a` | failed | False | 0.00% | 0.00% / N/A | 0.00% | 0.00% | 1 | 0.4537737 | 170.8s |

Cumulative reported evaluation cost: **0.4537737** platform units. The API does not identify a currency. The stop threshold is checked between messages, not within a message.

Not started: **4**. Historical preservation passed: **True**.

## Read-only failure diagnosis

Stopped after evaluation 1: search leads were retrieved only after catalog browsing. Native question capture rejected the search-order violation; no continuation or later conversation was started.

Actual provider emission: **5 native questions**, with **100.0%** question recall against the five unavailable contexts. Product-persisted questions: **0**.

Frozen rule failure: `lead_retrieval_before_catalog`. Catalog event **29** preceded the first document retrieval at event **31**.

The raw frozen result JSON is unchanged. Provider emission is separate diagnostic evidence; it does not convert the failed product run into a successful clarification or final reconciliation.


## Interpretation

Primary metrics use reconciled final labels. Failed/unreconciled findings are excluded from trusted predictions, so missing classifications remain misses; provisional observations are retained separately in JSON. Undefined precision is N/A with an explicit sample count. An incomplete conversation can still have valid per-document reconciliation, five honest UNKNOWN findings and a protected manual-only CRM correction.

The parent-step number is a reserved allowance. Observed LLM rounds are distinct stream correlation IDs, not a provider-certified consumed-parent-step counter. Nested MCP calls are measured separately.

These are five empirical executions on one 24-document fictional corpus, not a general production reliability guarantee.

## Evidence and reproduction

- Frozen configuration: `evaluations/reliability-v1-protocol.json`.
- Machine-readable report: `evaluations/reliability-v1-results.json`.
- Raw canonical run/finding/tool/audit evidence: five isolated `data/reliability-v1/evaluation-*.db` files.
- Private factual fixture, dispatch markers, provider read-backs and logs: `.tools/reliability-v1/`.
- Redacted execution traces: `runs/impact_<phase-run-id>.json`.
- UI: `http://127.0.0.1:8401/#/reliability`.
- Regenerate this Markdown: `python -B -m scripts.render_reliability_report`.
- Scoring definitions, safety limits and preparation commands: `docs/RELIABILITY.md`.
