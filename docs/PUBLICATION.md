# Public repository provenance

This repository preserves all 23 original Undrift development milestones. In an independent publication copy, author and committer emails were replaced with the GitHub noreply address verified against the authenticated account. Author names, messages, timestamps, parent relationships and every milestone's tracked source tree were preserved.

The original final delivery checkpoint `f2106b3` corresponds to public checkpoint `1b9803ff4a7f61f1d5af54dd6c80d2abd92f6632`. A separate publication commit adds this note and its README link, and updates the legacy migration tests to load the identical historical Store blob by its stable object ID. This keeps those tests runnable after commit IDs change, without weakening their checks. The full original-to-public hash mapping remains private and is not required to run the application.

## Historical identifiers

Original Undrift commit hashes in frozen evaluation protocols, reports and deployment records are provenance identifiers from the original development repository. They may not resolve in this privacy-sanitized history. They are deliberately retained: changing frozen evidence to reference later publication commits would misrepresent the executed study.

External Shiftly storefront commits, GitHub PR identities, Sites source/version identities, Cominty message IDs, execution outcomes and evaluation costs are unchanged. The five Reliability-v2 trials still include both failed trials. The publication process did not run agents, alter the jury deployment or change Shiftly production.

## Running and inspecting the source

Follow [SETUP.md](../SETUP.md) for backend and UI dependencies. Real credentials, databases, raw execution logs, private answer fixtures and local deployment packages are excluded. The committed `.env.example` contains no usable credentials. The evaluation ground-truth file under `tests/data/` is intentional reproducibility material; it is not exposed through agent MCP tools.

The [public jury demo](https://undrift-jury-demo.equalglass4.chatgpt.site) is a read-only historical replay. Rebuilding the operator application does not recreate private execution archives or deploy the jury snapshot. Source references to ignored local packages identify retained private artifacts, not downloadable repository contents.

## v4 update

Four subsequent development milestones (`0ae86c1`, `d3e24e1`, `a9e3a5c`, `ee2b163`) were transferred as reviewed source patches on top of the existing public history, each in its own noreply-authored commit. No original private commit objects or refs were imported. A separate release commit records the verified Sites v4 deployment and updates release-status documentation. The original development repository remains unchanged.

The final UI-only milestone `8242349` was likewise transferred by source patch, preserving the existing public history and publication-specific fixes. Its public milestone is `6d4dea50ce54c58c3464e6fbcbea57813f786e6b`; the subsequent release commit records Sites version 5 while keeping the same replay snapshot.
