# Undrift Reliability Evaluation v2

State: **completed**. Recorded conversations: **5/5**. Reconciled: **3**. Attempted paid messages: **8/10**.

Frozen protocol SHA-256: `ac244ecdc3ed033932ff46f8be80fa49f9dc51c2833d1a1307044ee068fa3cca`.

Five independently seeded stores use the same frozen instructions, model, tool configuration and rubric. Native question answers come from the precommitted private unavailable-facts fixture. The earlier development runs are excluded. No unfavorable result is replaced.

| Metric | Minimum | Mean | Maximum | Defined / recorded |
| --- | ---: | ---: | ---: | ---: |
| accuracy | 0.00% | 60.00% | 100.00% | 5/5 |
| false_positives | 0.00 | 0.00 | 0.00 | 5/5 |
| native_clarification_precision | 100.00% | 100.00% | 100.00% | 5/5 |
| native_clarification_recall | 100.00% | 100.00% | 100.00% | 5/5 |
| outdated_misses | 0.00 | 2.80 | 7.00 | 5/5 |
| outdated_precision | 100.00% | 100.00% | 100.00% | 3/5 |
| outdated_recall | 0.00% | 60.00% | 100.00% | 5/5 |
| paraphrase_recall | 0.00% | 60.00% | 100.00% | 5/5 |
| protected_classification_accuracy | 0.00% | 60.00% | 100.00% | 5/5 |
| retrieval_coverage | 100.00% | 100.00% | 100.00% | 5/5 |
| unknown_precision | 100.00% | 100.00% | 100.00% | 3/5 |
| unknown_recall | 0.00% | 60.00% | 100.00% | 5/5 |
| duration_s | 166.80 | 253.22 | 336.40 | 5/5 |
| reserved_parent_steps | 40.00 | 52.00 | 60.00 | 5/5 |
| observed_llm_rounds | 6.00 | 9.00 | 11.00 | 5/5 |
| native_question_count | 5.00 | 5.00 | 5.00 | 5/5 |
| mcp_error_count | 0.00 | 0.00 | 0.00 | 5/5 |

| Conversation | Root run | Final state | Reconciled | Accuracy | Outdated recall / precision | UNKNOWN recall | Native clarification recall | Messages | Reported cost | Duration |
| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | `run-3024c771eb3d14ec` | incomplete | True | 100.00% | 100.00% / 100.00% | 100.00% | 100.00% | 2 | 0.9063769 | 336.4s |
| 2 | `run-63b0c3097c73db9f` | failed | False | 0.00% | 0.00% / N/A | 0.00% | 100.00% | 1 | 0.39762525 | 166.8s |
| 3 | `run-b9509efbe12a27d7` | incomplete | True | 100.00% | 100.00% / 100.00% | 100.00% | 100.00% | 2 | 0.82557310 | 310.7s |
| 4 | `run-2f762cf348c744b8` | failed | False | 0.00% | 0.00% / N/A | 0.00% | 100.00% | 1 | 0.4312759 | 173.0s |
| 5 | `run-34a4671e7471540c` | incomplete | True | 100.00% | 100.00% / 100.00% | 100.00% | 100.00% | 2 | 0.72468625 | 279.2s |

Cumulative reported evaluation cost: **3.28553740** platform units. The API does not identify a currency. The stop threshold is checked between messages, not within a message.

Not started: **0**. Historical preservation passed: **True**.

Genuine persisted native questions: **25**; fixture answers: **15**. Protected-channel automation safety: **5/5**.
Local pending proposals: **18**. No proposal approval or publication is part of this study.

Per-conversation cost: minimum **0.39762525**, mean **0.65710748**, maximum **0.9063769** platform units.

## Discovery and clarification evidence

| Conversation | Questions / answered | Search / similarity calls | Lead before catalog | MCP errors | Pending proposals |
| --- | ---: | ---: | --- | ---: | ---: |
| 1 | 5 / 5 | 7 / 1 | True | 0 | 6 |
| 2 | 5 / 0 | 5 / 0 | False | 0 | 0 |
| 3 | 5 / 5 | 4 / 0 | True | 0 | 6 |
| 4 | 5 / 0 | 5 / 0 | False | 0 | 0 |
| 5 | 5 / 5 | 4 / 0 | True | 0 | 6 |

| Tool | Minimum calls | Mean calls | Maximum calls | Total calls |
| --- | ---: | ---: | ---: | ---: |
| get_document | 24 | 43.20 | 72 | 216 |
| get_policy | 1 | 1.80 | 3 | 9 |
| list_channels | 1 | 1.60 | 2 | 8 |
| record_finding | 19 | 33.40 | 43 | 167 |
| search_documents | 4 | 5.00 | 7 | 25 |
| similar_documents | 0 | 0.20 | 1 | 1 |

### Conversation 1: `run-3024c771eb3d14ec`

Thread: `674ca498-7a10-417b-8d36-4451cfa27dc7`. Database: `data/reliability-v2/evaluation-01.db` (identity `3afdc367a5384356a685c790303f2f83`).
Phase run IDs: `run-3024c771eb3d14ec`, `run-0b51f4fa84ca3788`.
Assistant message IDs: `b022447f-1f13-4759-b69b-c04883a173eb`, `6563254d-87b9-439f-a0b6-87494e7ab105`.

Native evidence integrity: **True**. Protected-channel automation safety: **True**.

Actual search queries:
- "return days refund period"
- "send back money customer changed mind"
- "before purchased earlier promised unless depends"
- "refund shipping fee returns window"
- "eligible exchange receipt weeks month"
- "statutory withdrawal right 14 days"
- "(catalog)"

Persisted phase/outcome notes:
- Native questions require reviewer answers
- documents require clarification or manual correction

### Conversation 2: `run-63b0c3097c73db9f`

Thread: `ac60d918-366a-4be1-b37d-90b58fd09539`. Database: `data/reliability-v2/evaluation-02.db` (identity `b4476b3881fd40daa5038bcf8c33d3f4`).
Phase run IDs: `run-63b0c3097c73db9f`.
Assistant message IDs: `9254d4bc-ee04-4d0e-8f95-9fe5189be979`.

Native evidence integrity: **True**. Protected-channel automation safety: **True**.

Actual search queries:
- "return days period"
- "60 days 30 days"
- "refund send back item customer changes mind"
- "if purchased earlier promised unless depends"
- "(catalog)"

Search certification: `search_strategy_violation`.

Persisted phase/outcome notes:
- discovery search-first contract was not satisfied

Unreconciled observations only: accuracy 79.17%; outdated recall 100.00%, precision 100.00%. These do not replace the frozen primary failure score.

### Conversation 3: `run-b9509efbe12a27d7`

Thread: `2623c647-7c28-4ba4-acf1-aec3b20bae2d`. Database: `data/reliability-v2/evaluation-03.db` (identity `8dc2d31c35a1455daa0165899d154cf0`).
Phase run IDs: `run-b9509efbe12a27d7`, `run-403c6955efac8a29`.
Assistant message IDs: `29ba6f4e-82d5-4e71-bad7-11ba9f1675e0`, `fd9d45e5-864d-433e-8a74-07285afebfa2`.

Native evidence integrity: **True**. Protected-channel automation safety: **True**.

Actual search queries:
- "depending on purchase date promotion offer earlier promised"
- "voluntary return period days"
- "send back items change of mind refund"
- "(catalog)"

Persisted phase/outcome notes:
- Native questions require reviewer answers
- documents require clarification or manual correction

### Conversation 4: `run-2f762cf348c744b8`

Thread: `b328d7f8-f695-451b-8a3b-f281832ccd71`. Database: `data/reliability-v2/evaluation-04.db` (identity `831461e7d45e47429bf5d1da42c4a899`).
Phase run IDs: `run-2f762cf348c744b8`.
Assistant message IDs: `524ca67e-6531-44ad-8fe1-f0dc1800079b`.

Native evidence integrity: **True**. Protected-channel automation safety: **True**.

Actual search queries:
- "withdrawal statutory right"
- "return days refund period"
- "ordered before earlier promised depends"
- "send back change your mind money back"
- "(catalog)"

Search certification: `search_strategy_violation`.

Persisted phase/outcome notes:
- discovery search-first contract was not satisfied

Unreconciled observations only: accuracy 79.17%; outdated recall 100.00%, precision 100.00%. These do not replace the frozen primary failure score.

### Conversation 5: `run-34a4671e7471540c`

Thread: `71570d23-c580-4740-921f-789deed43269`. Database: `data/reliability-v2/evaluation-05.db` (identity `a2fd8cc4ae444492bdfa70bb674f7e59`).
Phase run IDs: `run-34a4671e7471540c`, `run-f87de0038ce80bc2`.
Assistant message IDs: `721781bc-75c5-4713-aa9e-3939de9a55c1`, `275adb21-369d-4851-a94c-318b51847c62`.

Native evidence integrity: **True**. Protected-channel automation safety: **True**.

Actual search queries:
- "depending order earlier promised promotion eligible"
- "return days refund period"
- "send back items after buying customer changed mind"
- "(catalog)"

Persisted phase/outcome notes:
- Native questions require reviewer answers
- documents require clarification or manual correction

## Interpretation

Primary metrics use reconciled final labels. Failed/unreconciled findings are excluded from trusted predictions, so missing classifications remain misses; provisional observations are retained separately in JSON. Undefined precision is N/A with an explicit sample count. An incomplete conversation can still have valid per-document reconciliation, five honest UNKNOWN findings and a protected manual-only CRM correction.

The parent-step number is a reserved allowance. Observed LLM rounds are distinct stream correlation IDs, not a provider-certified consumed-parent-step counter. Nested MCP calls are measured separately.

These are five empirical executions on one 24-document fictional corpus, not a general production reliability guarantee.

## Evidence and reproduction

- Frozen configuration: `evaluations/reliability-v2-protocol.json`.
- Machine-readable report: `evaluations/reliability-v2-results.json`.
- Raw canonical run/finding/tool/audit evidence: five isolated `data/reliability-v2/evaluation-*.db` files.
- Private factual fixture, dispatch markers, provider read-backs and logs: `.tools/reliability-v2/`.
- Redacted execution traces: `runs/impact_<phase-run-id>.json`.
- UI: `http://127.0.0.1:8401/#/reliability`.
- Regenerate this Markdown: `python -B -m scripts.render_reliability_report --series reliability-v2`.
- Scoring definitions, safety limits and preparation commands: `docs/RELIABILITY.md`.
