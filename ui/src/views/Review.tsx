import { ArrowRight, CircleCheck, CircleX, TriangleExclamationPoint } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import type { ImpactDocument, ImpactSnapshot, Proposal, ProposalDetail, ProposalStatus } from "../api/types";
import { LogRow } from "../components/ActivityLog";
import { DiffView } from "../components/DiffView";
import { ReviewDecision } from "../components/ReviewDecision";
import { PublishingDecision } from "../components/PublishingDecision";
import { CopyText, DocVersion, EmptyState, ErrorState, LoadingBlock, PolicyVersion, ProposalState } from "../components/ui";
import { useImpact, useProposals } from "../lib/data";
import { formatDate, formatDateTime, humanizeActor, relativeTime, shortId } from "../lib/format";
import { navigate, useNow, useResource } from "../lib/hooks";
import { useLive } from "../lib/live";

const FILTERS: { key: ProposalStatus | "all"; label: string }[] = [
  { key: "pending_review", label: "Open" },
  { key: "approved", label: "Approved" },
  { key: "all", label: "All" },
];

export function ReviewView({ selectedId }: { selectedId: string | null }) {
  const { data, error, reload } = useProposals();
  const impact = useImpact();
  const now = useNow();
  const [filter, setFilter] = useState<ProposalStatus | "all">("all");
  const all = [...(data?.proposals ?? [])].sort((a, b) => b.created_at.localeCompare(a.created_at));
  const shown = filter === "all" ? all : all.filter((p) => p.status === filter);
  const activeId = selectedId ?? all.find((p) => p.status === "pending_review")?.id ?? all[0]?.id ?? null;
  const docs = impact.data?.documents ?? [];

  if (error && !data) return <ErrorState error={error} onRetry={reload} />;
  if (!data) return <LoadingBlock rows={10} />;

  return (
    <div className="review">
      <nav className="queue" aria-label="Proposals">
        <header className="queue-head">
          <h1>Review</h1>
          <div className="seg seg-sm" role="tablist" aria-label="Proposal status">
            {FILTERS.map((f) => {
              const n = f.key === "all" ? all.length : all.filter((p) => p.status === f.key).length;
              return (
                <button key={f.key} role="tab" aria-selected={filter === f.key} className={filter === f.key ? "is-active" : ""} onClick={() => setFilter(f.key)}>
                  {f.label} <span className="count">{n}</span>
                </button>
              );
            })}
          </div>
        </header>
        {shown.length === 0 ? (
          <EmptyState title="No proposals">Corrections appear here when an agent run proposes them.</EmptyState>
        ) : (
          <ul className="queue-list">
            {shown.map((p) => {
              const doc = docs.find((d) => d.id === p.document_id);
              return (
                <li key={p.id}>
                  <button className={`queue-item${p.id === activeId ? " is-active" : ""}`} onClick={() => navigate("review", p.id)} aria-current={p.id === activeId ? "true" : undefined}>
                    <span className="queue-line">
                      <span className="queue-title">{doc?.title ?? p.document_id}</span>
                      <span className="queue-age">{relativeTime(p.created_at, now)}</span>
                    </span>
                    <span className="queue-line">
                      <ProposalState status={p.status} />
                      <span className="queue-ids">
                        <code>{p.document_id}</code> <DocVersion version={p.base_version} />
                      </span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </nav>
      {activeId ? (
        <ProposalPane key={activeId} id={activeId} snap={impact.data} doc={docs.find((d) => d.id === all.find((p) => p.id === activeId)?.document_id)} />
      ) : (
        <div className="review-empty">
          <EmptyState title="Nothing to review">When an agent proposes a correction, it appears here with its diff and evidence.</EmptyState>
        </div>
      )}
    </div>
  );
}

function ProposalPane({ id, snap, doc }: { id: string; snap: ImpactSnapshot | null; doc: ImpactDocument | undefined }) {
  const { latestSeq } = useLive();
  const [local, setLocal] = useState<Proposal | null>(null);
  const { data, error, reload } = useResource((signal) => api.proposal(id, signal), `${id}:${latestSeq}:${local?.status ?? ""}`);
  if (error && !data) return <div className="review-center"><ErrorState error={error} onRetry={reload} /></div>;
  if (!data) return <div className="review-center"><LoadingBlock rows={8} /></div>;
  return <ProposalBody detail={data} snap={snap} doc={doc} onChanged={setLocal} />;
}

function ProposalBody({ detail, snap, doc, onChanged }: {
  detail: ProposalDetail;
  snap: ImpactSnapshot | null;
  doc: ImpactDocument | undefined;
  onChanged: (p: Proposal) => void;
}) {
  const { freshSince } = useLive();
  const now = useNow();
  const { proposal: p, checks, review_flags: flags } = detail;
  const prov = p.provenance;
  const evidence = detail.evidence ?? p.evidence ?? "";
  const evidenceVerified = Boolean(evidence && p.original_content.includes(evidence));
  const runId = p.run_id ?? prov.run_id;
  const policy = snap?.policy_versions.find((v) => v.version === p.policy_version) ?? null;
  const channel = snap?.channels.find((c) => c.id === doc?.channel);
  const open = p.status === "pending_review" || p.status === "approved";

  return (
    <>
      <section className="review-center" aria-label={`Proposal ${p.id}`}>
        <header className="rc-head">
          <div className="rc-title">
            <h2>{doc?.title ?? p.document_id}</h2>
            <ProposalState status={p.status} />
          </div>
          <p className="rc-meta">
            {channel?.name ?? doc?.channel} · <code>{p.document_id}</code> · <DocVersion version={p.base_version} /> →{" "}
            {p.applied_version ? <DocVersion version={p.applied_version} /> : <span className="muted">next version on apply</span>} ·{" "}
            <PolicyVersion version={p.policy_version} current={checks.policy_current} /> ·{" "}
            {p.created_by.startsWith("agent:") ? "proposed by Cominty agent" : `proposed by ${humanizeActor(p.created_by)}`}{" "}
            <span title={formatDateTime(p.created_at)}>{relativeTime(p.created_at, now)}</span>
          </p>
        </header>
        <DiffView before={p.original_content} after={p.proposed_content} unified={p.diff} baseVersion={p.base_version} documentId={p.document_id} />
        <ReviewDecision detail={detail} onChanged={onChanged} />
        <PublishingDecision detail={detail} />
      </section>

      <aside className="review-side" aria-label="Context">
        {open && (
          <section className="side-block">
            <h3>Validation</h3>
            <ul className="checks">
              <Check ok={checks.document_unchanged} label={`Document still at v${p.base_version}, text unchanged`} bad={`Document is now v${checks.document_current_version}`} />
              <Check ok={checks.policy_current} label={`Policy still at P${p.policy_version}`} bad={`Policy is now P${checks.policy_current_version}`} />
              <Check ok label="Server guardrails passed at submission" />
              {flags.map((f, i) => (
                <li key={i} className="check is-warn">
                  <TriangleExclamationPoint size={13} aria-hidden /> {f.flag.replaceAll("_", " ")}
                  {f.sentence !== undefined && ` (sentence ${f.sentence + 1})`}
                </li>
              ))}
            </ul>
          </section>
        )}

        <section className="side-block">
          <h3>Agent reasoning</h3>
          <p className="reasoning">{p.reason}</p>
        </section>

        <section className="side-block">
          <h3>Original evidence</h3>
          {evidence ? (
            <>
              <blockquote className="quote">{evidence}</blockquote>
              <p className={`verify ${evidenceVerified ? "is-ok" : "is-warn"}`}>
                {evidenceVerified ? <CircleCheck size={13} aria-hidden /> : <TriangleExclamationPoint size={13} aria-hidden />}
                {evidenceVerified ? `Verbatim in stored v${p.base_version}` : "Not found verbatim in the stored document"}
              </p>
            </>
          ) : (
            <p className="muted">No evidence quote recorded for this proposal's run.</p>
          )}
        </section>

        <section className="side-block">
          <h3>Policy reference</h3>
          {policy ? (
            <div className="policy-ref">
              <p>
                <PolicyVersion version={policy.version} current={checks.policy_current} /> <strong>{policy.voluntary_return_days} days</strong> for
                purchases from {formatDate(policy.effective_from)}
              </p>
              <p className="muted small">{policy.description}</p>
            </div>
          ) : (
            <p className="muted">
              <PolicyVersion version={p.policy_version} /> <code>{p.policy_id}</code>
            </p>
          )}
        </section>

        <section className="side-block">
          <h3>Provenance</h3>
          <dl className="facts">
            <dt>Source</dt>
            <dd>{prov.source ?? "—"}</dd>
            <dt>Agent</dt>
            <dd>{prov.agent_id ? <code>{prov.agent_id}</code> : "—"}</dd>
            <dt>Thread</dt>
            <dd>{prov.thread_id ? <CopyText value={prov.thread_id} display={shortId(prov.thread_id, 13)} label="thread id" /> : "—"}</dd>
            <dt>Run</dt>
            <dd>
              {runId ? (
                <a href={`#/activity/${encodeURIComponent(runId)}`}>
                  {shortId(runId)} <ArrowRight size={11} aria-hidden />
                </a>
              ) : (
                "—"
              )}
            </dd>
            <dt>Proposal</dt>
            <dd>
              <CopyText value={p.id} label="proposal id" />
            </dd>
          </dl>
        </section>

        <section className="side-block">
          <h3>Audit trail</h3>
          <ul className="log log-compact">
            {[...detail.events].reverse().map((e) => (
              <LogRow key={e.seq} event={e} fresh={e.seq > freshSince} />
            ))}
          </ul>
        </section>
      </aside>
    </>
  );
}

function Check({ ok, label, bad }: { ok: boolean; label: string; bad?: string }) {
  return (
    <li className={`check ${ok ? "is-ok" : "is-bad"}`}>
      {ok ? <CircleCheck size={13} aria-hidden /> : <CircleX size={13} aria-hidden />} {ok ? label : bad ?? label}
    </li>
  );
}
