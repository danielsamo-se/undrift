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
- **Guard outgoing replies:** a separate, tool-less Cominty Guard agent validates responses against strict versioned contracts. The server rejects anything stale, unbound or malformed, and fails closed.
- **Remember with versions:** verified verdicts go into native Cominty Memory, bound to the policy version. They can inform later checks but never decide them.

We measured it honestly. In a frozen five-trial study, three trials reached 100% classification accuracy and two failed a search-order contract. Primary accuracy is 60% with the failures counted, all 25 clarification questions were captured, and there were zero false positives. The public demo is read-only recorded evidence from these real runs, with every step labelled as a real Cominty execution, a human decision, production verification, a server rule or a local fixture.

## 2–3 minute recording plan

Record the public site: https://undrift-jury-demo.equalglass4.chatgpt.site (or the local v3 preview until it is deployed). Use a 1440-pixel-wide window, browser zoom 100%, and no other tabs.

| # | Time | Screen and action | Narration (short) | Evidence type |
|---|---|---|---|---|
| 1 | 0:00–0:20 | **Jury guide** hero. Hold on "One policy changes. Every channel stays right." Slowly scroll past the 60 → 30 days panel and the metrics row. | "When a policy changes, the old version survives in every channel. Undrift finds that drift, asks for missing facts, prepares controlled fixes and verifies the published correction." | — |
| 2 | 0:20–0:40 | Click **02 · Undrift finds the drift** to open the Impact Map (discovery archive). Hover two documents: one outdated, one existing commitment. | "A Cominty Managed Agent searched 24 documents across seven channels through our MCP server. Every finding quotes stored evidence. Protected promises are recognised, not overwritten." | Real Cominty run |
| 3 | 0:40–1:00 | Guide, then **03 · It asks instead of guessing**. Open the clarification panel and show one question and its answer. | "When a fact is missing, like an order's purchase date, the agent asks native Cominty questions. The reviewer answers and the same conversation continues." | Real Cominty run plus human answer |
| 4 | 1:00–1:20 | **04** opens the Review queue: show a diff and the validation checklist. Then open **Channel autonomy** (operator archive) and point at Legal (manual only), Help Center (bounded auto, max 1) and Website (pull request). | "Every fix is an exact patch pinned to document and policy versions. Each channel has its own server-enforced limits. Legal stays manual." | Server rule (configuration recorded) |
| 5 | 1:20–1:45 | **05/06** opens the publishing review. Show the stepper Approved → PR → Merged → Deployed → Verified, then click **Open pull request #1**, and finally **View Shiftly Returns & Refunds**. | "An approved website patch became a real GitHub pull request. A human merged it, Vercel deployed it, and Undrift verified that production serves exactly the approved bytes." | GitHub + Vercel production |
| 6 | 1:45–2:05 | **07** opens the Guard check `gchk-79d5…`. Point at "Real Cominty Guard execution, verified by the server", the bindings and the cost. Briefly show the recorded list with its deterministic labels. | "Outgoing replies go through a separate Guard agent with no tools. The server checks every binding and fails closed. This ALLOW is a real Cominty execution. The blocks are deterministic rules, and they are labelled that way." | Real Cominty run plus server rules |
| 7 | 2:05–2:20 | **08** opens Memory & Slack. Show the Cominty Memory card: version, policy v2 binding and an identical read-back. | "The verified verdict lives in native Cominty Memory, bound to policy version 2. A later check recalled it, but memory can never release text on its own." | Cominty Memory read-back |
| 8 | 2:20–2:45 | **09** opens Reliability v2. Show 3 of 5 reconciled, 60% primary accuracy, the two failed trials and 0 false positives. | "We measured it with five frozen, paid trials. Three were perfect, two failed our search contract, and they count. That is 60%, with zero false positives." | Frozen evaluation |
| 9 | 2:45–3:00 | Back to the guide hero, or the live Shiftly returns page showing 30 days. | "One policy change, every channel checked, and only verified, human-authorized corrections reach customers. That's Undrift." | — |

**Limitations to mention (one sentence each, steps 4, 6 and 8):**
- Automatic application is shown with a labelled local fixture.
- Guard has one recorded live check.
- The study is small and includes two failures.

**Do not say:**
- that Slack is connected;
- that agents are running live on the public site;
- that the blocked Guard cases came from the Cominty agent.

## Remaining owner decisions

1. **Deploy the prepared v3 jury release** (`.tools/jury-site-v3`) through the existing Sites workflow, then run `.tools/verify_jury_v3.py https://undrift-jury-demo.equalglass4.chatgpt.site`.
2. **Push** the repository to GitHub, if the repository should be shared.
3. **Submit** the description and video.
