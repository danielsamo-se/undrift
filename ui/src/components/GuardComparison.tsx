import type { GuardCheck, Policy } from "../api/types";
import { formatDate, formatDateTime, shortId } from "../lib/format";
import { navigate } from "../lib/hooks";

/** Real, server-verified Cominty Guard executions only. Deterministic and fail-closed checks are excluded. */
export function realGuardDecisions(checks: GuardCheck[]): GuardCheck[] {
  return checks
    .filter((c) => c.decision_source === "cominty_guard" && c.execution.mode === "cominty" && c.execution.status === "success" && c.verification.ok)
    .sort((a, b) => a.created_at.localeCompare(b.created_at));
}

/** True when only the AI Guard caught the problem: the recorded precheck found nothing, the verified verdict blocked. */
export function caughtOnlyByGuard(c: GuardCheck): boolean {
  return c.precheck.verdict === "CLEAR" && c.decision === "BLOCK" && c.decision_source === "cominty_guard";
}

const VERDICT: Record<string, string> = { ALLOW: "Allow", BLOCK: "Block", NEEDS_REVIEW: "Needs review" };

function Card({ check }: { check: GuardCheck }) {
  const ex = check.execution;
  const finding = check.agent_output?.findings[0];
  const only = caughtOnlyByGuard(check);
  return (
    <article className={`gc-card gc-${check.decision.toLowerCase()}${only ? " is-highlight" : ""}`} data-testid="guard-compare-card">
      <header className="gc-head">
        <span className={`guard-decision guard-d-${check.decision.toLowerCase()}`}>{VERDICT[check.decision] ?? check.decision}</span>
        <span className="gc-src">Real Cominty Guard · verified by the server</span>
      </header>
      <blockquote className="gc-candidate">“{check.candidate_redacted}”</blockquote>
      <dl className="gc-rows">
        <dt>Deterministic precheck</dt>
        <dd>
          <strong className={check.precheck.verdict === "CLEAR" ? "" : `guard-d-${check.precheck.verdict.toLowerCase()}`}>{check.precheck.verdict}</strong>
          {check.precheck.verdict === "CLEAR" ? " · no rule fired (recorded result)" : ` · ${check.precheck.findings.map((f) => f.rule.replaceAll("_", " ")).join(", ")}`}
        </dd>
        <dt>Cominty Guard verdict</dt>
        <dd><strong className={`guard-d-${check.decision.toLowerCase()}`}>{check.agent_output?.verdict ?? check.decision}</strong>
          {finding && <> · <code>{finding.assessment}</code> citing {finding.evidence_refs.join(", ")}</>}</dd>
        <dt>Rationale</dt>
        <dd>{finding?.reason ?? "not recorded"}</dd>
        <dt>Server verification</dt>
        <dd>{check.verification.ok ? "Passed: check ID, candidate hash, policy binding, exact quotes and references" : check.verification.problems.join("; ")}</dd>
        <dt>Release</dt>
        <dd className={check.release_eligible ? "gc-ok" : "gc-bad"}>{check.release_eligible ? "Release-eligible (nothing sent; release is a separate action)" : "Not release-eligible"}</dd>
        <dt>Provenance</dt>
        <dd className="gc-prov">
          <code>{check.id}</code> · agent <code>{ex.agent_id}</code>
          {ex.message_id && <> · message <code>{shortId(ex.message_id, 8)}</code></>}
          <br />{ex.models?.join(", ") || "model not reported"}
          {ex.duration_s != null && ` · ${ex.duration_s.toFixed(1)} s`}
          {ex.cost?.total != null && ` · reported cost ${ex.cost.total}`}
          {` · ${ex.tool_calls?.length ? `tool calls: ${ex.tool_calls.join(", ")}` : "no tool calls"}`}
          <br /><span className="muted">{formatDateTime(check.created_at)}</span>
        </dd>
      </dl>
      {only && (
        <p className="gc-callout" data-testid="guard-only-catch">
          <strong>Caught only by the AI Guard.</strong> No deterministic rule fired on this text when it was checked: string matching
          alone would not have flagged it. The Cominty Guard recognised the semantic contradiction: {finding?.reason}
        </p>
      )}
      <button type="button" className="text-btn" onClick={() => navigate("guard", check.id)}>Open the full check →</button>
    </article>
  );
}

export function GuardComparison({ checks, policy }: { checks: GuardCheck[]; policy: Policy | null }) {
  const real = realGuardDecisions(checks);
  if (real.length < 2) return null;
  const verdicts = [...new Set(real.map((c) => c.decision))];
  return (
    <section className="gc section" aria-label="Real Cominty Guard decisions" data-testid="guard-compare">
      <div className="gc-intro">
        <h2 className="guard-h2">{real.length} real Cominty Guard decisions{verdicts.length > 1 ? `: ${verdicts.map((v) => VERDICT[v] ?? v).join(" and ")}` : ""}</h2>
        <p className="muted">Same tool-less Guard agent, same verified policy, different candidate texts. Each verdict was accepted only after the server verified its bindings.</p>
        {policy && (
          <p className="gc-policy"><span>Current policy</span> <code>{policy.id}</code> v{policy.version}: <strong>{policy.voluntary_return_days} days</strong> for purchases from {formatDate(policy.effective_from)}; earlier promises kept; statutory 14-day right unchanged.</p>
        )}
      </div>
      <div className="gc-grid">{real.map((c) => <Card key={c.id} check={c} />)}</div>
      <p className="muted small">A CLEAR precheck never allows anything on its own and does not establish that a text is semantically correct; it can only escalate. Precheck results shown are those recorded at the time of each check.</p>
    </section>
  );
}
