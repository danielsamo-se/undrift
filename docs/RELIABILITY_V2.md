# Reliability v2: frozen, awaiting paid authorization

This is a fresh study, separate from the stopped `reliability-v1` conversation.
No paid v2 messages have been attempted. No original databases or v1 artifacts
were migrated, backfilled, regraded or replaced.

## Frozen artifacts

- Implementation checkpoint: `87dabc394bc32d5359a828c0fa887d786cc308e7`.
- Lifecycle fix: `799782d824c58fd6dcce2140f6642e1d9d65ed6b`.
- Protocol: `evaluations/reliability-v2-protocol.json`.
- Protocol SHA-256: `ac244ecdc3ed033932ff46f8be80fa49f9dc51c2833d1a1307044ee068fa3cca`.
- Initial frozen report: `evaluations/reliability-v2-results.json`.
- Normalized instruction SHA-256: `ed03b7f22724bab6ec7a900952bdc05547d22694c0de73a1dc01811a8c9ba787`.
- Model: Claude Sonnet 5.5 (High); sole MCP connection `undrift_mcp_ngrok`, always active.
- Source files, staged frontend assets, deterministic corpus, rubric, answer fixture,
  capability configuration, tool schemas and the private five-store plan are hashed.
  Source/plan/UI changes fail the frozen checks rather than silently revising the study.
- JSON report schema remains `reliability-v1`; study identity is `reliability-v2`.

The private plan/active configuration and factual answers are under
`.tools/reliability-v2/`. Credentials remain in existing private configuration.
Do not print the complete MCP URL, copy fixtures into the corpus, or commit these
private files. The fresh answer fixture is byte-identical to v1's precommitted fixture.

## Fresh deterministic stores

Every store contains 24 current documents across seven channels, policy history and
document versions. Each begins with zero runs, findings, conversations, questions,
proposals or publications. Domains are identical; identities and future credentials
are isolated. Historical evidence is not imported.

| Slot | Database | Identity |
| --- | --- | --- |
| 1 | `data/reliability-v2/evaluation-01.db` | `3afdc367a5384356a685c790303f2f83` |
| 2 | `data/reliability-v2/evaluation-02.db` | `b4476b3881fd40daa5038bcf8c33d3f4` |
| 3 | `data/reliability-v2/evaluation-03.db` | `8dc2d31c35a1455daa0165899d154cf0` |
| 4 | `data/reliability-v2/evaluation-04.db` | `831461e7d45e47429bf5d1da42c4a899` |
| 5 | `data/reliability-v2/evaluation-05.db` | `a2fd8cc4ae444492bdfa70bb674f7e59` |

Slot 1 is active on demo MCP `127.0.0.1:8001` and Review API/UI
`http://127.0.0.1:8401/#/reliability`. Public MCP uses the existing ngrok hostname
`chatter-poker-theorize.ngrok-free.dev` and unchanged private path. Request inspection
remains disabled. Only the verified demo MCP/API processes were replaced; archive
8101, publishing 8301, storefront 8200 and ngrok 4040 retained their original owners.

The UI defaults to v2 and labels v1 as archived in a read-only selector. Its two
reports, statuses, denominators and diagnoses remain separate. API selection uses
`GET /api/reliability?series=reliability-v1`; it never rewrites archived report bytes.

## Completed free checks

The complete `run_impact` no-agent preflight returned 0 with a GET-only Cominty SDK
transport. Remote instructions/model/allowlist were checked without changing them.
Public discovery returned exactly:

`health_check`, `get_policy`, `get_document`, `list_channels`, `search_documents`,
`similar_documents`, `record_finding`.

All six scoped tools rejected missing and invalid credentials. The legacy read
schemas retain optional credential fields for archive compatibility; this unified
database enforces both credentials at runtime. No proposal, approval, application or
publishing tool is exposed. Review-session authentication succeeds with the existing
private token and rejects missing credentials. Private fixture, rubric and instruction
file routes return 404 through both the UI and ngrok origin.

API and MCP report the same database identity. Running source fingerprints and all
served JS/CSS/index bytes match the frozen build. Real headless-browser verification
confirmed the v2 active label, v1 archive option, frozen protocol hash, five unstarted
slots and no historical v1 conversation in the v2 display.

An explicitly labelled **offline regression replay** loaded the five genuine native
question payloads from the unchanged historical trace into a disposable scratch
database. Exact prompts/options persisted despite the reproduced strategy violation;
the phase/conversation stayed failed, continuation was blocked and no proposals were
created. This is not a new native provider execution or an evaluation result. The
five evaluation stores remain empty. Live platform behavior must be observed during
the next separately authorized execution.

Preparation checks passed: 385 backend tests, 45 subsequent lifecycle/freeze/API
checks, 63 UI tests, TypeScript, production build, public no-agent preflight and final
frozen attestation. The later targeted set includes the new plan/UI tamper check.
All 47 protected historical/configuration/evidence files remained byte-identical.
Private verification reports are under `.tools/reliability-v2/`.

## Limits for the separately authorized execution

- Five independent conversations, sequentially, with no replacements or retries.
- At most one same-thread continuation per conversation: ten paid messages maximum.
- Initial turn: 40 parent steps. Continuation: 20. At most 60 reserved parent steps
  per evaluated conversation; nested MCP calls are counted separately.
- At most 12 search/catalog and four optional similarity calls across both phases.
  Search-order certification remains unchanged.
- Stop before another message when reported v2 cumulative cost reaches 5 platform
  units, or when cost is unknown. The existing conversation-level cost gate is 2
  units before a continuation. These are between-message gates, not in-message caps.
- Fixture answers are supplied only for genuine admissible native questions in an
  eligible conversation. A strategy-failed conversation receives neither answers nor
  continuation. Its failure remains in all denominators.
- Safety, authentication, isolation, provider/evidence consistency, configuration
  drift or interruption stops the series. No unfavorable result is discarded.

V1's reported `0.4537737` units remain in its archived study. Starting v2 does not
grant paid authorization; the operator must explicitly approve the new study's
allowance. No paid execution is authorized by this preparation checkpoint.

After explicit approval, execute once from the repository root:

```powershell
.\.venv\Scripts\python.exe -B -m scripts.reliability_evaluation execute --series reliability-v2 --paid
```

The executor rechecks the public database and frozen configuration before every
message and switches only the same approved demo ports between the five stores.
Never remove its future execution lock/attempt markers or rerun after a stop. No
remote agent settings, production Shiftly content, GitHub writes or publication
actions are part of this evaluation.
