import { ArrowRight, CircleCheck, Globe, Lock, TriangleExclamationPoint, X } from "lucide-react";
import { useEffect, useRef } from "react";
import type { Channel, ImpactDocument, Policy } from "../api/types";
import { PREFILTER_LABEL, isStale, statusOf } from "../lib/classification";
import { highlight } from "../lib/diff";
import { formatDateTime, relativeTime, shortId } from "../lib/format";
import { navigate } from "../lib/hooks";
import { DocVersion, PolicyVersion, ProposalState, Status } from "./ui";

export function DocumentPanel({ doc, channel, policy, onClose }: {
  doc: ImpactDocument;
  channel: Channel | undefined;
  policy: Policy;
  onClose: () => void;
}) {
  const status = statusOf(doc);
  const f = doc.finding;
  const parts = highlight(doc.content, f?.evidence && doc.content.includes(f.evidence) ? f.evidence : null);
  const Audience = channel?.audience === "public" ? Globe : Lock;
  const ref = useRef<HTMLElement>(null);

  useEffect(() => {
    if (window.matchMedia?.("(max-width: 1080px)").matches) ref.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [doc.id]);

  return (
    <aside className="inspector" aria-label={`Details for ${doc.title}`} ref={ref}>
      <header className="insp-head">
        <div className="insp-title">
          <h2>{doc.title}</h2>
          <p className="insp-meta">
            <Audience size={12} aria-hidden /> {channel?.name ?? doc.channel} · <code>{doc.id}</code> · <DocVersion version={doc.version} />
          </p>
        </div>
        <button className="icon-btn" onClick={onClose} aria-label="Close details">
          <X size={15} />
        </button>
      </header>

      <div className="insp-block">
        <Status status={status} stale={isStale(doc)} />
        <p className="insp-desc">
          {f ? (
            <>
              Agent finding for <DocVersion version={f.document_version} /> under{" "}
              <PolicyVersion version={f.policy_version} current={f.policy_version === policy.version} />,{" "}
              <span title={formatDateTime(f.classified_at)}>{relativeTime(f.classified_at)}</span>
            </>
          ) : (
            "No agent has analysed this document yet."
          )}
        </p>
        {f && !f.current && (
          <p className="note note-warn">
            <TriangleExclamationPoint size={13} aria-hidden /> The document or policy changed after this finding. A new
            agent run is needed.
          </p>
        )}
      </div>

      {f?.action === "needs_clarification" && <p className="note note-warn">Human clarification required. Resolve the missing context in the finding reason before correcting this document.</p>}
      {f?.action === "manual_only" && <p className="note note-warn">Protected channel. Legal and CRM documents require a separate manual editing workflow; no automated correction is permitted.</p>}
      {f?.action === "needs_correction" && <p className="note note-warn">The finding is validated, but a safe correction still needs human review.</p>}
      {f?.verified_conclusion === false && f.action !== "needs_clarification" && <p className="note note-warn">Provisional {f.category} finding: this analysis has not established a verified conclusion.</p>}
      {Boolean(doc.provisional_findings?.length) && <div className="insp-block">
        <h3>Provisional findings</h3>
        {doc.provisional_findings?.map((item) => <details key={item.id}>
          <summary>{item.category} ? {item.run_status} ? <a href={`#/activity/${item.run_id}`}>{shortId(item.run_id)}</a></summary>
          {item.evidence && <blockquote className="quote">{item.evidence}</blockquote>}<p>{item.reason}</p>
        </details>)}
      </div>}
      <div className="insp-block">
        <h3>Published text</h3>
        <p className="published">
          {parts.map((p, i) => (p.mark ? <mark key={i}>{p.text}</mark> : <span key={i}>{p.text}</span>))}
        </p>
      </div>

      {f && (
        <div className="insp-block">
          <h3>Agent evidence</h3>
          {f.evidence ? (
            <>
              <blockquote className="quote">{f.evidence}</blockquote>
              <p className={`verify ${f.evidence_verified ? "is-ok" : "is-warn"}`}>
                {f.evidence_verified ? <CircleCheck size={13} aria-hidden /> : <TriangleExclamationPoint size={13} aria-hidden />}
                {f.evidence_verified
                  ? `Quote found verbatim in stored v${f.document_version}`
                  : `Quote not found verbatim in stored v${f.document_version ?? "?"}`}
              </p>
            </>
          ) : (
            <p className="muted">No evidence quote was retrieved for this finding. See the reason below.</p>
          )}
          {f.reason && <p className="reasoning">{f.reason}</p>}
          <dl className="facts">
            {f.run_status && <><dt>Run status</dt><dd>{f.run_status}</dd></>}
            <dt>Prefilter</dt>
            <dd>
              {PREFILTER_LABEL[f.prefilter ?? ""] ?? "—"}
              {f.agrees_with_prefilter === false && <span className="warn-text"> · differs from agent</span>}
            </dd>
            <dt>Run</dt>
            <dd>
              {f.run_id ? (
                <a href={`#/activity/${encodeURIComponent(f.run_id)}`}>
                  {shortId(f.run_id)} <ArrowRight size={11} aria-hidden />
                </a>
              ) : (
                "—"
              )}
            </dd>
          </dl>
        </div>
      )}

      <div className="insp-block">
        <h3>Corrections</h3>
        {doc.proposals.length ? (
          <ul className="plain-list">
            {doc.proposals.map((p) => (
              <li key={p.id}>
                <button className="row-link" onClick={() => navigate("review", p.id)}>
                  <ProposalState status={p.status} />
                  <span className="muted">
                    <DocVersion version={p.base_version} /> · <PolicyVersion version={p.policy_version} /> · {relativeTime(p.created_at)}
                  </span>
                  <ArrowRight size={13} aria-hidden className="row-link-arrow" />
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">None proposed.</p>
        )}
      </div>
    </aside>
  );
}
