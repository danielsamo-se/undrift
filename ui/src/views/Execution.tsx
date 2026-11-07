import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { ActivityEvent, Conversation, RunSummary } from "../api/types";
import { CopyText, EmptyState, ErrorState, LoadingBlock } from "../components/ui";
import { useRuns } from "../lib/data";
import {
  LANE_LABEL, PROVENANCE_LABEL, buildExecution, outcomeHeadline, provenanceOf, questionsFor, traceSummary,
  type Execution, type Lane, type Step,
} from "../lib/execution";
import { formatCost, formatDateTime, formatTime, plural, shortId } from "../lib/format";
import { navigate, useResource } from "../lib/hooks";
import { useJury } from "../lib/jury";

const LANES: Lane[] = ["agent", "backend", "human", "scripted", "fixture"];
const ITEMS_SHOWN = 6;

/** All events from the run's first sequence number onwards (bounded paging; recorded data only). */
async function loadRunEvents(run: RunSummary, signal: AbortSignal): Promise<ActivityEvent[]> {
  const out: ActivityEvent[] = [];
  let after = Math.max(0, run.event_range[0] - 1);
  for (let page = 0; page < 10; page++) {
    const res = await api.eventsAfter(after, 1000, signal);
    out.push(...res.events);
    if (!res.events.length || res.next_after >= res.latest_seq) break;
    after = res.next_after;
  }
  return out;
}

function defaultRun(runs: RunSummary[]): RunSummary | undefined {
  // Most recent real Cominty execution; otherwise the most recent run.
  return runs.find((r) => provenanceOf(r) === "cominty") ?? runs[0];
}

function StepRow({ step, questions }: { step: Step; questions: { prompt: string; answer: string | null; answered_by: string | null; document_id: string }[] }) {
  const [all, setAll] = useState(false);
  const items = all ? step.items : step.items.slice(0, ITEMS_SHOWN);
  const span = step.first.seq === step.last.seq ? `#${step.first.seq}` : `#${step.first.seq}–#${step.last.seq}`;
  return (
    <li className={`ex-step lane-${step.lane} st-${step.status}`} data-stage={step.stage} data-testid="ex-step">
      <div className="ex-step-time">
        <time dateTime={step.first.ts} title={formatDateTime(step.first.ts)}>{formatTime(step.first.ts)}</time>
        <span className="muted small">{span}</span>
      </div>
      <div className="ex-step-body">
        <p className="ex-step-lane">{LANE_LABEL[step.lane]}{step.status !== "ok" && <span className={`flag flag-${step.status}`}>{step.status}</span>}</p>
        <h3 className="ex-step-title">
          {step.lane === "agent" || step.lane === "fixture" ? <code>{step.title}</code> : step.title}
          {step.count > 1 && <span className="ex-count">×{step.count}</span>}
        </h3>
        <ul className="ex-items">{items.map((it, i) => <li key={i}>{it}</li>)}</ul>
        {step.items.length > ITEMS_SHOWN && (
          <button type="button" className="text-btn" aria-expanded={all} onClick={() => setAll(!all)}>
            {all ? "Show fewer" : `Show all ${step.items.length}`}
          </button>
        )}
        {step.note && <p className="muted small">{step.note}</p>}
        {step.title === "Native Cominty questions asked" && questions.length > 0 && (
          <ol className="ex-questions">
            {questions.map((q, i) => (
              <li key={i}><p>{q.prompt}</p>
                <p className="muted small">{q.answer ? <>Answer recorded{q.answered_by ? ` by ${q.answered_by}` : ""}: “{q.answer}”</> : "Unanswered"}</p></li>
            ))}
          </ol>
        )}
      </div>
    </li>
  );
}

function StageRail({ ex, status }: { ex: Execution; status: string }) {
  return (
    <ol className="ex-rail" aria-label="Execution stages">
      {ex.stages.map((st, i) => (
        <li key={st.id} className={`ex-stage is-${st.status}`} title={st.hint}>
          <button type="button" disabled={st.status === "none"} onClick={() => document.querySelector(`[data-stage="${st.id}"]`)?.scrollIntoView({ behavior: "smooth", block: "center" })}>
            <span className="ex-stage-n">{String(i + 1).padStart(2, "0")}</span>
            <span className="ex-stage-label">{st.label}</span>
            <span className="ex-stage-meta">
              {st.status === "none" ? "no record" : st.id === "outcome" ? `run ${status}` : st.status === "ok" ? `${st.count} recorded` : `${st.count} · ${st.status}`}
            </span>
            <span className="ex-stage-lanes" aria-hidden>{st.lanes.map((l) => <i key={l} className={`ex-dot lane-${l}`} />)}</span>
          </button>
        </li>
      ))}
    </ol>
  );
}

export function ExecutionView({ runId }: { runId: string | null }) {
  const jury = useJury();
  const runs = useRuns();
  const list = runs.data?.runs ?? [];
  const run = (runId ? list.find((r) => r.id === runId) : undefined) ?? (runId ? undefined : defaultRun(list));
  const events = useResource((signal) => (run ? loadRunEvents(run, signal) : Promise.resolve([])), run?.id ?? "none");
  const detail = useResource((signal) => (run ? api.run(run.id, signal) : Promise.resolve(null)), `detail:${run?.id ?? "none"}`);
  const convs = useResource((signal) => api.conversations(signal).catch(() => ({ conversations: [] as Conversation[] })), "conversations");
  const ex = useMemo(() => (run && events.data ? buildExecution(run, events.data) : null), [run, events.data]);
  const trace = traceSummary(detail.data?.run ?? null);
  const phases = (convs.data?.conversations ?? []).flatMap((c) => c.phases);
  const questions = run ? questionsFor(run.id, phases) : [];
  const siblings = run?.conversation_id ? list.filter((r) => r.conversation_id === run.conversation_id).sort((a, b) => (a.phase ?? 0) - (b.phase ?? 0)) : [];

  useEffect(() => { document.title = "Execution timeline · Undrift"; }, []);

  if (runs.error && !runs.data) return <ErrorState error={runs.error} onRetry={runs.reload} />;
  if (!runs.data) return <LoadingBlock rows={8} />;
  if (!list.length) return <div className="page"><EmptyState title="No agent runs in this database" /></div>;

  return (
    <div className="page ex-page">
      <header className="ph">
        <div>
          <h1>Execution timeline</h1>
          <p className="ph-sub">What actually happened in a run, rebuilt step by step from the persisted audit log. Every step names who produced it; nothing is inferred or simulated.</p>
        </div>
      </header>

      <nav className="ex-runs" aria-label="Runs">
        {list.map((r) => {
          const p = provenanceOf(r);
          return (
            <button key={r.id} type="button" className={`ex-run-pick prov-${p}${r.id === run?.id ? " is-current" : ""}`} aria-current={r.id === run?.id ? "true" : undefined}
              onClick={() => navigate("execution", r.id)}>
              <span className="ex-run-prov">{p === "cominty" ? "Cominty" : p === "fixture" ? "Fixture" : p === "diagnostic" ? "Diagnostic" : "Aborted"}</span>
              <code>{shortId(r.id)}</code>
              <span className="muted small">{r.status}{r.phase ? ` · phase ${r.phase}` : ""}{r.cost?.total ? ` · ${formatCost(r.cost.total)}` : ""}</span>
            </button>
          );
        })}
      </nav>

      {!run ? <EmptyState title="Run not found in this database" /> : (
        <>
          <section className={`ex-hero prov-${ex?.provenance ?? provenanceOf(run)}`} data-testid="ex-hero">
            <p className="ex-prov">{PROVENANCE_LABEL[ex?.provenance ?? provenanceOf(run)]}{jury.readonly && " · historical replay"}</p>
            <h2 className="ex-headline">{ex ? outcomeHeadline(run, ex) : "Loading the recorded steps…"}</h2>
            <dl className="ex-facts">
              <div><dt>Run</dt><dd><code>{run.id}</code></dd></div>
              <div><dt>Agent</dt><dd>{run.agent_id ? <code>{run.agent_id}</code> : "none"}</dd></div>
              <div><dt>Thread</dt><dd>{run.thread_id ? <CopyText value={run.thread_id} label="thread id" display={shortId(run.thread_id, 8)} /> : "not recorded"}</dd></div>
              <div><dt>Model</dt><dd>{trace.models.length ? trace.models.join(", ") : "not reported"}</dd></div>
              <div><dt>Duration</dt><dd>{run.duration_s != null ? `${run.duration_s} s` : "not recorded"}</dd></div>
              <div><dt>Reported cost</dt><dd>{run.cost?.total ? formatCost(run.cost.total) : "not recorded"}</dd></div>
              <div><dt>Started</dt><dd>{formatDateTime(run.started_at)}</dd></div>
              <div><dt>Status</dt><dd className={`ex-status is-${run.status}`}>{run.status}</dd></div>
            </dl>
            {ex && (
              <dl className="ex-metrics">
                <div><dt>{Object.values(ex.toolCalls).reduce((a, b) => a + b, 0)}</dt><dd>MCP tool calls</dd></div>
                <div><dt>{ex.documentsRetrieved.length}/{run.document_ids?.length ?? '?'}</dt><dd>documents retrieved</dd></div>
                <div><dt>{Object.keys(ex.findings).length}</dt><dd>findings recorded</dd></div>
                <div><dt>{ex.questions}</dt><dd>native questions</dd></div>
                <div><dt>{ex.proposals.length}</dt><dd>proposals</dd></div>
                <div><dt>{ex.autoApplied.length}</dt><dd>applied under delegation</dd></div>
              </dl>
            )}
            {siblings.length > 1 && (
              <p className="small ex-phases">Same Cominty conversation:{" "}
                {siblings.map((r) => <button key={r.id} type="button" className={`link${r.id === run.id ? " is-current" : ""}`} onClick={() => navigate("execution", r.id)}>phase {r.phase} <code>{shortId(r.id)}</code></button>)}
              </p>
            )}
          </section>

          {events.error && !events.data ? <ErrorState error={events.error} onRetry={events.reload} /> : !ex ? <LoadingBlock rows={6} /> : (
            <>
              <StageRail ex={ex} status={run.status} />
              <p className="ex-legend" aria-label="Lane legend">
                {LANES.filter((l) => ex.steps.some((st) => st.lane === l)).map((l) => <span key={l} className={`ex-legend-item lane-${l}`}><i className={`ex-dot lane-${l}`} />{LANE_LABEL[l]}</span>)}
              </p>
              <div className="ex-layout">
                {ex.steps.length ? <ol className="ex-steps">{ex.steps.map((st, i) => <StepRow key={i} step={st} questions={questions} />)}</ol>
                  : <EmptyState title="No step-level events were recorded for this run">Only the run summary above is available.</EmptyState>}
                <aside className="ex-side">
                  <section className="panel ex-panel">
                    <h3>MCP tools used</h3>
                    {Object.keys(ex.toolCalls).length ? <ul className="plain-list ex-tools">{Object.entries(ex.toolCalls).sort((a, b) => b[1] - a[1]).map(([t, n]) => <li key={t}><code>{t}</code><span>{n}</span></li>)}</ul>
                      : <p className="muted small">No MCP tool calls were correlated with this run.</p>}
                    <p className="muted small">Counted from the MCP server's own call log, not from the agent's report.</p>
                  </section>
                  <section className="panel ex-panel">
                    <h3>Cominty-reported steps</h3>
                    {detail.data?.run.trace.length ? <>
                      <p className="small">{plural(trace.modelSteps, "model step")}{trace.models.length ? ` · ${trace.models.join(", ")}` : ""}</p>
                      {trace.tools.length > 0 && <ol className="ex-trace">{trace.tools.map((t, i) => <li key={i}><code>{t.tool}</code> {t.label}{t.status !== "success" && <span className="flag flag-error">{t.status}</span>}</li>)}</ol>}
                      <p className="muted small">Model reasoning text is intentionally not shown; only tool and delegation labels as reported by Cominty.</p>
                    </> : <p className="muted small">{ex.provenance === "cominty" ? "No Cominty stream record was saved for this run." : "Not applicable: no Cominty execution."}</p>}
                  </section>
                  <section className="panel ex-panel">
                    <h3>Inspect further</h3>
                    <ul className="plain-list">
                      <li><a href={`#/activity/${encodeURIComponent(run.id)}`}>Raw audit events in Activity →</a></li>
                      {ex.proposals.map((p) => <li key={p}><a href={`#/review/${encodeURIComponent(p)}`}>Proposal <code>{p}</code> →</a></li>)}
                    </ul>
                  </section>
                </aside>
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}
