# Submission materials

Nothing in this file has been submitted. Submission and any external posting need the owner's approval.

## Challenge description (draft)

**Undrift keeps every customer channel consistent when a policy changes.**

When a company changes a policy, such as shortening returns from 60 to 30 days, the old wording survives in help articles, chatbot knowledge, support macros, order e-mails, CRM notes and legal pages. Some of those mentions are wrong now. Others are promises to existing customers that must not be changed. Finding and fixing them by hand is slow and error-prone, and an AI that "fixes" everything would break real commitments.

Undrift puts Cominty Managed Agents in charge of the reasoning and the server in charge of authority:

- **Discover:** the Impact Agent searches 24 documents across seven channels through Undrift's MCP server. It records only findings that quote stored evidence verbatim.
- **Clarify:** when a fact is missing, the agent asks native Cominty clarification questions instead of guessing. The reviewer's answers continue the same conversation.
- **Correct under control:** each fix is an exact, version-pinned patch. Channel autonomy is enforced on the server: legal and CRM stay manual, Help Center may auto-apply one verified change per run, and the website changes only through a human-merged pull request.
- **Publish and verify:** an approved patch became GitHub PR #1. A human merged it, Vercel deployed it, and Undrift verified that production serves exactly the approved text.
- **Guard outgoing replies:** a separate, tool-less Cominty Guard agent validates responses against strict versioned contracts. In a real check it blocked “eight weeks to return”, a 56-day claim that no deterministic rule caught at the time, and it allowed a correct reply. The server verifies every binding and fails closed.
- **Remember with versions:** verified verdicts go into native Cominty Memory, bound to the policy version. They can inform later checks but never decide them.

We measured it honestly. In a frozen five-trial study, three trials reached 100% classification accuracy and two failed a search-order contract. Primary accuracy is 60% with the failures counted, all 25 clarification questions were captured, and there were zero false positives. An execution timeline rebuilds each real run from the audit log, step by step. The public demo is read-only recorded evidence from these real runs, with every step labelled as a real Cominty execution, a human decision, production verification, a server rule or a local fixture.

## 2–3 minute recording plan

**Which site to record:**
- **If v4 is deployed:** record https://undrift-jury-demo.equalglass4.chatgpt.site.
- **If it is not:** record the identical prepared replay locally at http://127.0.0.1:8602. Start it with `.tools\node\node.exe .tools\jury-preview-v4.mjs`, and say "the prepared release, running locally" once.

Use a 1440-pixel-wide window at 100% zoom with no other tabs. Click **Start the walkthrough** once, then move with **Next** in the walkthrough bar. It shows the step number and what to notice, so you never need to return to the guide. Skip a step with its number button.

| # | Time | Screen and action | Narration (short) | Evidence type |
|---|---|---|---|---|
| 1 | 0:00–0:15 | **Jury guide** hero, the 60 → 30 days panel and the metrics row. | "When a policy changes, the old version survives in every channel. Undrift finds that drift, asks for missing facts, prepares controlled fixes and verifies the result." | — |
| 2 | 0:15–0:30 | Step **01** shows the policy strip (P2 = 30 days, was 60); **Next** (02) opens the Impact Map. Hover one outdated document and one protected commitment. | "A Cominty Managed Agent searched 24 documents across seven channels through our MCP server. Protected promises are recognised, not overwritten." | Real Cominty run |
| 3 | 0:30–0:55 | **03** opens the **Execution timeline**. Hold on the hero, then the stage rail, then scroll through the search → retrieval → record_finding steps to the five native questions. | "This is what the agent actually did, rebuilt from the audit log: every MCP search and retrieval, every finding the server accepted, and the questions it asked instead of guessing. Each step says whether the agent, our backend or a human produced it." | Real Cominty run plus server rules |
| 4 | 0:55–1:10 | **05** opens the Review queue: a diff and its checklist. Then open **Channel autonomy** and point at the “What is real here” note. | "Every fix is an exact, version-pinned patch, with server-enforced limits per channel. A real delegated run failed our search contract, so nothing was applied. Automatic application is shown only in a labelled fixture." | Server rule |
| 5 | 1:10–1:35 | **06** opens the publishing review: Approved → PR → Merged → Deployed → Verified, then **Open pull request #1**. **Next** (07) opens the Overview publishing panel: hold on the five recorded stages and the “Two different operations” note. | "An approved patch became a real GitHub pull request. A human merged it, Vercel deployed it, and Undrift verified that production serves exactly the approved bytes. Publishing the website and editing Undrift's own copy are separate, explicit operations." | GitHub + Vercel production |
| 6 | 1:35–2:05 | **08** opens Guard. Show the two cards side by side, then the red “Caught only by the AI Guard” callout. | "A separate Guard agent with no tools checks outgoing replies. It allowed the correct 30-day answer. It blocked “eight weeks”: 56 days, which no string rule caught. The server verified both verdicts, and we have since hardened the rule too." | Real Cominty runs |
| 7 | 2:05–2:15 | **09** Memory: version, policy v2 binding and the identical read-back. | "Verified verdicts live in native Cominty Memory, bound to the policy version, and can never release text on their own." | Cominty Memory read-back |
| 8 | 2:15–2:40 | **10** opens Reliability v2: 3 of 5 reconciled, 60%, the two failed trials, 0 false positives. | "Five frozen paid trials. Three perfect, two failed our search contract, and they count. Sixty percent, zero false positives." | Frozen evaluation |
| 9 | 2:40–3:00 | Back to the guide hero, or the live Shiftly returns page showing 30 days. | "One policy change, every channel checked, and only verified, human-authorized corrections reach customers. That's Undrift." | — |

**Limitations to mention (one sentence each, in shots 4, 6 and 8):**
- No real Cominty run has applied a change automatically; the real attempt failed closed.
- Guard has two recorded live checks.
- The study is small and includes two failures.

**Do not say:**
- that Slack is connected;
- that agents are running live on the public site;
- that the deterministic Guard cases came from the Cominty agent;
- that a real Cominty run applied a Help Center change automatically;
- that the precheck guarantees semantic correctness.

## Remaining owner decisions

Sites v4 is deployed and publicly verified with 13 archives, both real Guard verdicts, the failed delegated run and the execution timeline. See `docs/PUBLIC_DEMO.md` and `deployment/jury/deployment.json`. The four new milestones are transferred through the privacy-safe publication process.

1. **Record** the demonstration using the plan above.
2. **Submit** the description and video.
