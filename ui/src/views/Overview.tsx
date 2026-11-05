import { ArrowRight, Terminal } from "lucide-react";
import { useMemo, useState } from "react";
import { useReviewerSession } from "../api/session";
import type { ImpactSnapshot, Proposal } from "../api/types";
import { LogRow } from "../components/ActivityLog";
import { ImpactBoard } from "../components/ImpactBoard";
import { PolicyChangeDialog } from "../components/PolicyChangeDialog";
import { UnlockDialog } from "../components/ReviewerSession";
import { Disclosure, DocVersion, ErrorState, LoadingBlock, PolicyVersion, Section, Status } from "../components/ui";
import { consistency, liveDocuments, nextAction, runInProgress } from "../lib/board";
import { isStale, statusOf } from "../lib/classification";
import { useImpact, useProposals } from "../lib/data";
import { groupOf } from "../lib/events";
import { formatCost, formatDate, formatDateTime, humanizeActor, plural, relativeTime, shortId } from "../lib/format";
import { navigate, useNow } from "../lib/hooks";
import { useLive } from "../lib/live";

export function OverviewView() {
  const impact = useImpact();
  const proposals = useProposals();
  if (impact.error && !impact.data) return <ErrorState error={impact.error} onRetry={impact.reload} />;
  if (!impact.data) return <LoadingBlock rows={10} />;
  return <Overview snap={impact.data} proposals={proposals.data?.proposals ?? []} reload={impact.reload} />;
}

function Overview({ snap, proposals, reload }: { snap: ImpactSnapshot; proposals: Proposal[]; reload: () => void }) {
  const now = useNow(1000);
  const live = useLive();
  const session = useReviewerSession();
  const [dialog, setDialog] = useState<"change" | "unlock" | null>(null);
  const c = consistency(snap);
  const next = nextAction(snap, proposals);
  const { policy } = snap;
  const previous = snap.policy_versions.find((v) => v.version === policy.version - 1);
  const drifted = snap.documents.filter((d) => statusOf(d) === "outdated" && !isStale(d));
  const unresolved = snap.documents.filter((d) => {
    const s = statusOf(d);
    return s === "outdated" || s === "unknown" || s === "unanalyzed" || isStale(d);
  });
  const recent = useMemo(
    () => live.events.filter((e) => groupOf(e.type) !== "mcp" && groupOf(e.type) !== "system").slice(-5).reverse(),
    [live.events],
  );
  const run = snap.latest_run;
  const liveDocs = useMemo(() => liveDocuments(live.events, live.freshSince, now), [live.events, live.freshSince, now]);
  const tone = c.drifted || c.unknown ? "bad" : c.stale || c.unanalyzed ? "warn" : "ok";

  return (
    <div className="page">
      <section className="policy-strip" aria-label="Authoritative policy">
        <div className="ps-main">
          <PolicyVersion version={policy.version} current />
          <span className="ps-title">{snap.policy_title}</span>
          <span className="ps-fact">
            <strong>{policy.voluntary_return_days} days</strong> for purchases from {formatDate(policy.effective_from)}
            {previous && <span className="muted"> · was {previous.voluntary_return_days} days in P{previous.version}</span>}
          </span>
        </div>
        <button className="btn btn-sm" onClick={() => setDialog(session ? "change" : "unlock")}>
          Change policy
        </button>
        <Disclosure summary="Authoritative text and version history">
          <blockquote className="policy-text">{policy.description}</blockquote>
          <table className="table">
            <thead>
              <tr>
                <th>Version</th>
                <th>Return period</th>
                <th>Effective from</th>
                <th>Created</th>
                <th>By</th>
              </tr>
            </thead>
            <tbody>
              {[...snap.policy_versions].reverse().map((v) => (
                <tr key={v.version}>
                  <td>
                    <PolicyVersion version={v.version} current={v.version === policy.version} />
                  </td>
                  <td>{v.voluntary_return_days} days</td>
                  <td>{formatDate(v.effective_from)}</td>
                  <td>{formatDateTime(v.created_at)}</td>
                  <td>{humanizeActor(v.created_by)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Disclosure>
      </section>

      <section className={`state state-${tone}`} aria-label="Consistency status">
        <div className="state-main">
          <p className="state-title">
            {c.drifted
              ? `${plural(c.drifted, "document has", "documents have")} drifted from P${policy.version}`
              : c.unknown
                ? `${plural(c.unknown, "document", "documents")} could not be classified`
                : c.stale || c.unanalyzed
                  ? `${plural(c.stale + c.unanalyzed, "document needs", "documents need")} analysis against P${policy.version}`
                  : `All ${c.total} documents are consistent with P${policy.version}`}
          </p>
          {drifted.length > 0 && (
            <p className="state-sub">
              {drifted.map((d, i) => (
                <span key={d.id}>
                  {i > 0 && ", "}
                  <button className="text-btn" onClick={() => navigate("impact", d.id)}>
                    {d.title}
                  </button>
                </span>
              ))}{" "}
              still {drifted.length === 1 ? "states" : "state"} a superseded return period.
            </p>
          )}
          <ul className="state-counts">
            <li>
              <b>{c.consistent}</b> consistent
            </li>
            <li className={c.drifted ? "is-bad" : ""}>
              <b>{c.drifted}</b> drifted
            </li>
            <li className={c.pendingReview ? "is-accent" : ""}>
              <b>{c.pendingReview}</b> awaiting review
            </li>
            {c.awaitingApply > 0 && (
              <li>
                <b>{c.awaitingApply}</b> approved, not applied
              </li>
            )}
            {c.unknown > 0 && (
              <li className="is-warn">
                <b>{c.unknown}</b> unknown
              </li>
            )}
            {c.stale + c.unanalyzed > 0 && (
              <li className="is-warn">
                <b>{c.stale + c.unanalyzed}</b> not current
              </li>
            )}
          </ul>
        </div>
        <div className="next">
          <p className="next-label">Next action</p>
          {next.kind === "review" && (
            <button className="btn btn-primary" onClick={() => navigate("review", next.proposalId)}>
              Review correction: {next.title} <ArrowRight size={13} aria-hidden />
            </button>
          )}
          {next.kind === "apply" && (
            <button className="btn btn-primary" onClick={() => navigate("review", next.proposalId)}>
              Apply approved correction: {next.title} <ArrowRight size={13} aria-hidden />
            </button>
          )}
          {next.kind === "analyse" && (
            <p className="next-text">
              <Terminal size={13} aria-hidden /> {next.reason}. Run <code>run_impact.py --run</code>.
            </p>
          )}
          {next.kind === "none" && <p className="next-text">Nothing needs attention.</p>}
        </div>
      </section>

      <div className="overview-grid">
        <Section
          title="Impact"
          aside={
            <button className="text-btn" onClick={() => navigate("impact")}>
              Open impact map <ArrowRight size={12} aria-hidden />
            </button>
          }
        >
          <ImpactBoard snapshot={snap} onSelect={(id) => navigate("impact", id)} live={liveDocs} analysing={runInProgress(live.events)} compact />
        </Section>

        <div className="overview-side">
          <Section title="Unresolved">
            {unresolved.length ? (
              <ul className="plain-list">
                {unresolved.map((d) => (
                  <li key={d.id}>
                    <button className="row-link" onClick={() => navigate("impact", d.id)}>
                      <Status status={statusOf(d)} stale={isStale(d)} short />
                      <span className="row-link-title">{d.title}</span>
                      <DocVersion version={d.version} />
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted">Nothing unresolved.</p>
            )}
          </Section>

          <Section
            title="Agent activity"
            aside={
              <button className="text-btn" onClick={() => navigate("activity")}>
                All activity <ArrowRight size={12} aria-hidden />
              </button>
            }
          >
            {run ? (
              <button className="run-card" onClick={() => navigate("activity", run.id)}>
                <span className={`run-dot run-dot-${run.status === "running" ? "running" : run.errors.length ? "error" : "ok"}`} aria-hidden />
                <span className="run-card-main">
                  <span className="run-card-title">
                    {run.status === "running" ? "Agent run in progress" : `Last run ${relativeTime(run.finished_at ?? run.started_at, now)}`}
                    <code className="muted">{shortId(run.id)}</code>
                  </span>
                  <span className="run-card-sub">
                    {plural(run.mcp_calls.length, "MCP call")} · {plural(Object.keys(run.classifications).length, "finding")} ·{" "}
                    {plural(run.proposals.length, "proposal")}
                    {run.duration_s !== null && ` · ${run.duration_s} s`}
                    {run.cost?.total && ` · cost ${formatCost(run.cost.total)}`}
                    {run.retrieval && (run.retrieval.complete ? " · retrieval verified" : " · retrieval incomplete")}
                  </span>
                </span>
              </button>
            ) : (
              <p className="muted">No agent run recorded yet.</p>
            )}
            {recent.length > 0 && (
              <ul className="log log-compact">
                {recent.map((e) => (
                  <LogRow key={e.seq} event={e} fresh={e.seq > live.freshSince} />
                ))}
              </ul>
            )}
          </Section>
        </div>
      </div>

      <footer className="sysline">
        <span>{snap.activity.mcp_calls} MCP calls recorded</span>
        <span>{snap.activity.events} events</span>
        <span>{plural(snap.runs_total, "agent run")}</span>
        <span>{snap.channels.length} channels</span>
        <span>Local SQLite · loopback API</span>
      </footer>

      {dialog === "change" && <PolicyChangeDialog policy={policy} onClose={() => setDialog(null)} onChanged={reload} />}
      {dialog === "unlock" && <UnlockDialog onClose={() => setDialog(null)} />}
    </div>
  );
}
