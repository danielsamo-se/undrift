import { Activity, GitPullRequest, LayoutGrid, Waypoints, ShieldCheck } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useEffect } from "react";
import { ReviewerControl } from "./components/ReviewerSession";
import { useProposals } from "./lib/data";
import { relativeTime } from "./lib/format";
import { navigate, useNow, useRoute, type View } from "./lib/hooks";
import { LiveEventsProvider, useLive } from "./lib/live";
import { ActivityView } from "./views/Activity";
import { ImpactView } from "./views/Impact";
import { OverviewView } from "./views/Overview";
import { ReviewView } from "./views/Review";
import { ReliabilityView } from './views/Reliability';
import { JuryProvider, JuryBanner, JuryGuide, useJury } from './lib/jury';

const NAV: { view: View; label: string; icon: LucideIcon; key: string }[] = [
  { view: "overview", label: "Overview", icon: LayoutGrid, key: "1" },
  { view: "impact", label: "Impact map", icon: Waypoints, key: "2" },
  { view: "review", label: "Review", icon: GitPullRequest, key: "3" },
  { view: "activity", label: "Activity", icon: Activity, key: "4" },
  { view: 'reliability', label: 'Reliability', icon: ShieldCheck, key: '5' },
];

export function Mark({ size = 20 }: { size?: number }) {
  return (
    <svg className="mark" width={size} height={size} viewBox="0 0 20 20" aria-hidden>
      <rect x="0.5" y="0.5" width="19" height="19" rx="5" fill="var(--ink)" />
      <path d="M5 7.5h10" stroke="var(--mark-muted)" strokeWidth="1.6" strokeLinecap="round" />
      <path d="M5 12.5h10" stroke="#fff" strokeWidth="1.6" strokeLinecap="round" />
      <circle cx="13.2" cy="7.5" r="1.7" fill="var(--s-outdated)" />
    </svg>
  );
}

function Shell() {
  const jury=useJury();
  const route = useRoute();
  const live = useLive();
  const now = useNow(5000);
  const proposals = useProposals();
  const pending = proposals.data?.proposals.filter((p) => p.status === "pending_review").length ?? 0;
  const lastMcp = [...live.events].reverse().find((e) => e.type === "mcp.tool_call");

  useEffect(() => {
    document.title = `${NAV.find((n) => n.view === route.view)?.label ?? "Undrift"} · Undrift`;
  }, [route.view]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (e.metaKey || e.ctrlKey || e.altKey || t?.closest("input, textarea, select, [role=dialog]")) return;
      const hit = NAV.find((n) => n.key === e.key);
      if (hit) navigate(hit.view);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="shell">
      <aside className="side">
        <a className="brand" href="#/overview" aria-label="Undrift overview">
          <Mark />
          <span className="brand-name">Undrift</span>
        </a>
        <p className="workspace">
          Shiftly <span>fictional workspace</span>
        </p>
        <nav className="nav" aria-label="Main">
          {jury.readonly && <a className={`nav-item${route.view==='tour'?' is-active':''}`} href="#/tour">Jury guide</a>}
          {NAV.map(({ view, label, icon: Icon, key }) => (
            <a key={view} href={`#/${view}`} className={`nav-item${route.view === view ? " is-active" : ""}`} aria-current={route.view === view ? "page" : undefined}>
              <Icon size={15} aria-hidden />
              <span className="nav-label">{label}</span>
              {view === "review" && pending > 0 && <span className="nav-count" aria-label={`${pending} awaiting review`}>{pending}</span>}
              {view === "activity" && <span className={`nav-live is-${live.status}`} aria-hidden />}
              <kbd className="nav-key" aria-hidden>
                {key}
              </kbd>
            </a>
          ))}
        </nav>
        <div className="side-foot">
          {jury.readonly ? <p className="muted small">Read-only historical evidence</p> : <ReviewerControl />}
          <dl className="sys">
            <div>
              <dt>API</dt>
              <dd className={`is-${live.status}`}>
                {live.status === "live" ? jury.readonly?'replay ready':`synced ${live.lastSync ? relativeTime(new Date(live.lastSync).toISOString(), now) : ""}` : live.status}
              </dd>
            </div>
            <div>
              <dt>MCP</dt>
              <dd>{lastMcp ? jury.readonly?'recorded calls':`last call ${relativeTime(lastMcp.ts, now)}` : "no calls loaded"}</dd>
            </div>
          </dl>
        </div>
      </aside>
      <main className="main">
        <JuryBanner />
        {jury.readonly && route.view==='tour' && <JuryGuide />}
        {route.view === "overview" && <OverviewView />}
        {route.view === "impact" && <ImpactView selectedId={route.param} />}
        {route.view === "review" && <ReviewView selectedId={route.param} />}
        {route.view === "activity" && <ActivityView focusRun={route.param} />}
        {route.view === 'reliability' && <ReliabilityView />}
      </main>
    </div>
  );
}

export function App() {
  return (
    <JuryProvider><LiveEventsProvider>
      <Shell />
    </LiveEventsProvider></JuryProvider>
  );
}
