import { api } from "../api/client";
import type { Proposal, Publication } from "../api/types";
import { formatDateTime, shortId } from "../lib/format";
import { navigate, useResource } from "../lib/hooks";

/** Recorded publishing-journal stages, in order. A stage is shown only when its event exists. */
const STAGES: { type: string; label: string }[] = [
  { type: "publishing.approved", label: "Human-reviewed correction approved for publication" },
  { type: "publishing.pr_created", label: "GitHub pull request created" },
  { type: "publishing.merged", label: "Merged by a human in GitHub" },
  { type: "publishing.deployed", label: "Vercel production deployment observed" },
  { type: "publishing.verified", label: "Public text verified against the approved bytes" },
];

async function firstPublication(candidates: Proposal[], signal: AbortSignal): Promise<{ proposal: Proposal; publication: Publication } | null> {
  for (const proposal of candidates) {
    const res = await api.publication(proposal.id, signal).catch(() => null);
    if (res?.publication && res.publication.events.length) return { proposal, publication: res.publication };
  }
  return null;
}

/** Evidence-backed external publication status, distinct from local document state. */
export function PublishedEvidence({ proposals, notCurrent }: { proposals: Proposal[]; notCurrent: number }) {
  const candidates = proposals.filter((p) => p.status === "approved" || p.status === "applied");
  const key = candidates.map((p) => p.id).join(",");
  const res = useResource((signal) => (candidates.length ? firstPublication(candidates, signal) : Promise.resolve(null)), `pub:${key}`);
  const found = res.data;
  if (!found) return null;
  const { proposal, publication: pub } = found;
  const at = (type: string) => pub.events.find((e) => e.type === type);
  const ev = pub.verification_evidence;
  const page = ev && ev.http_statuses["/returns/"] === 200 ? `${pub.target.site_url.replace(/\/$/, "")}/returns/` : pub.target.site_url;
  const verified = pub.status === "verified";
  return (
    <section className={`pubev${verified ? " is-verified" : ""}`} aria-label="External publication" data-testid="published-evidence">
      <header className="pubev-head">
        <p className="pubev-kicker">External publication · recorded publishing journal</p>
        <h2>{verified ? "Published to the live website and verified" : `Publication status: ${pub.status.replace("_", " ")}`}</h2>
        <p className="muted">
          Correction <code>{proposal.id}</code> for <code>{proposal.document_id}</code> → <code>{pub.target.repository}</code> <code>{pub.target.path}</code>
        </p>
      </header>
      <ol className="pubev-steps">
        {STAGES.map((st) => {
          const e = at(st.type);
          return (
            <li key={st.type} className={e ? "is-done" : "is-missing"}>
              <span className="pubev-dot" aria-hidden />
              <span className="pubev-label">{st.label}</span>
              <span className="pubev-meta">
                {!e ? "not recorded" : <>
                  {formatDateTime(e.ts)}
                  {st.type === "publishing.approved" && e.metadata.actor ? <> · by {String(e.metadata.actor)}</> : null}
                  {st.type === "publishing.pr_created" && pub.pr ? <> · <a href={pub.pr.url} target="_blank" rel="noreferrer">PR #{pub.pr.number} ↗</a></> : null}
                  {st.type === "publishing.merged" && pub.merge_commit ? <> · commit <code>{shortId(pub.merge_commit, 7)}</code></> : null}
                  {st.type === "publishing.deployed" && ev ? <> · build commit <code>{shortId(ev.observed_build_commit, 7)}</code></> : null}
                  {st.type === "publishing.verified" && ev ? <> · sha256 <code>{shortId(ev.rendered_policy_sha256, 12)}</code> {ev.rendered_policy_sha256 === ev.approved_content_sha256 ? "matches the approved text" : "does not match"}</> : null}
                </>}
              </span>
            </li>
          );
        })}
      </ol>
      <p className="pubev-links">
        {pub.pr && <a className="btn btn-sm" href={pub.pr.url} target="_blank" rel="noreferrer">Open pull request #{pub.pr.number} ↗</a>}
        <a className="btn btn-sm" href={page} target="_blank" rel="noreferrer">View the live page ↗</a>
        <button type="button" className="btn btn-sm" onClick={() => navigate("review", proposal.id)}>Full publishing audit →</button>
      </p>
      <div className="pubev-explain">
        <p><strong>Two different operations.</strong> External publication changed the live storefront through GitHub and Vercel, as recorded above.
          Local application would create a new version of Undrift’s own copy of <code>{proposal.document_id}</code>; it was not performed. That is why this archive
          still lists the correction as “approved, not applied” and keeps the local document at its earlier version.</p>
        {notCurrent > 0 && <p>The “not current” count concerns local analysis: these findings were recorded without the version-bound retrieval that
          current Undrift requires (for example by an earlier version), so Overview suggests re-analysis. It says nothing about the published website.</p>}
      </div>
    </section>
  );
}
