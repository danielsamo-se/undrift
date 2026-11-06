import { Search, X } from "lucide-react";
import { useMemo, useState } from "react";
import type { ImpactSnapshot } from "../api/types";
import { DocumentPanel } from "../components/DocumentPanel";
import { ImpactBoard } from "../components/ImpactBoard";
import { ErrorState, LoadingBlock, PolicyVersion, StatusDot } from "../components/ui";
import { consistency, liveDocuments } from "../lib/board";
import { STATUS, STATUS_ORDER, type MapStatus } from "../lib/classification";
import { useImpact } from "../lib/data";
import { navigate, useNow } from "../lib/hooks";
import { useLive } from "../lib/live";

export function ImpactView({ selectedId }: { selectedId: string | null }) {
  const { data, error, reload } = useImpact();
  if (error && !data) return <ErrorState error={error} onRetry={reload} />;
  if (!data) return <LoadingBlock rows={10} />;
  return <Impact snap={data} selectedId={selectedId} />;
}

function Impact({ snap, selectedId }: { snap: ImpactSnapshot; selectedId: string | null }) {
  const live = useLive();
  const now = useNow(1000);
  const [status, setStatus] = useState<MapStatus | null>(null);
  const [query, setQuery] = useState("");
  const c = consistency(snap);
  const filter = useMemo(() => ({ statuses: status ? new Set([status]) : null, query }), [status, query]);
  const liveDocs = useMemo(() => liveDocuments(live.events, live.freshSince, now), [live.events, live.freshSince, now]);
  const selected = snap.documents.find((d) => d.id === selectedId) ?? null;
  const select = (id: string | null) => navigate("impact", id);

  return (
    <div className="page">
      <header className="ph">
        <div>
          <h1>Impact map</h1>
          <p className="ph-sub">
            <PolicyVersion version={snap.policy.version} current /> → {snap.documents.length} documents in {snap.channels.length} channels ·
            latest agent classification per document
          </p>
        </div>
      </header>

      <div className="toolbar">
        <label className="search">
          <Search size={13} aria-hidden />
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Filter documents" aria-label="Filter documents by title, id or text" />
          {query && (
            <button className="icon-btn icon-btn-sm" onClick={() => setQuery("")} aria-label="Clear filter">
              <X size={12} />
            </button>
          )}
        </label>
        <div className="seg seg-wrap" role="toolbar" aria-label="Filter by classification">
          <button className={!status ? "is-active" : ""} aria-pressed={!status} onClick={() => setStatus(null)}>
            All <span className="count">{c.total}</span>
          </button>
          {STATUS_ORDER.filter((s) => c.counts.get(s)).map((s) => (
            <button key={s} className={status === s ? "is-active" : ""} aria-pressed={status === s} onClick={() => setStatus(status === s ? null : s)} title={STATUS[s].description}>
              <StatusDot status={s} /> {STATUS[s].short} <span className="count">{c.counts.get(s)}</span>
            </button>
          ))}
        </div>
      </div>

      <div className={`impact${selected ? " has-inspector" : ""}`}>
        <ImpactBoard
          snapshot={snap}
          filter={filter}
          selectedId={selectedId}
          onSelect={select}
          live={liveDocs}
          analysing={(snap.active_run_ids?.length ?? 0) > 0}
        />
        {selected && (
          <DocumentPanel
            doc={selected}
            channel={snap.channels.find((ch) => ch.id === selected.channel)}
            policy={snap.policy}
            onClose={() => select(null)}
          />
        )}
      </div>
    </div>
  );
}
