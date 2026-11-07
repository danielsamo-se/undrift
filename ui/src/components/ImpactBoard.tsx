import { Globe, Lock } from "lucide-react";
import type { ImpactDocument, ImpactSnapshot } from "../api/types";
import { STATUS, isStale, provisionalCategory, statusOf } from "../lib/classification";
import { buildLanes, consistency, type BoardFilter, type LiveKind } from "../lib/board";
import { formatDate } from "../lib/format";
import { DocVersion, PolicyVersion, StatusDot } from "./ui";

const LIVE_LABEL: Record<LiveKind, string> = {
  reading: "read by agent",
  classified: "classified",
  proposed: "correction proposed",
};

interface Props {
  snapshot: ImpactSnapshot;
  filter?: BoardFilter;
  selectedId?: string | null;
  onSelect: (id: string | null) => void;
  live?: Map<string, LiveKind>;
  analysing?: boolean;
  compact?: boolean;
}

function DocCard({ doc, selected, live, compact, onSelect }: {
  doc: ImpactDocument;
  selected: boolean;
  live: LiveKind | undefined;
  compact: boolean;
  onSelect: (id: string | null) => void;
}) {
  const status = statusOf(doc);
  const stale = isStale(doc);
  const provisional = provisionalCategory(doc);
  const open = doc.proposals.filter((p) => p.status === "pending_review" || p.status === "approved");
  return (
    <button
      type="button"
      className={`dcard dcard-${status}${selected ? " is-selected" : ""}${live ? " is-live" : ""}`}
      aria-pressed={selected}
      aria-label={`${doc.title}, ${STATUS[status].label}${provisional ? `, agent said ${STATUS[provisional].label}, unverified` : ""}${stale ? ", stale" : ""}, document version ${doc.version}${open.length ? `, ${open.length} correction open` : ""}`}
      onClick={() => onSelect(selected ? null : doc.id)}
    >
      <span className="dcard-row">
        <StatusDot status={status} />
        <span className="dcard-status">{STATUS[status].short}</span>
        {stale && <span className="stale">stale</span>}
        {provisional && <span className="dcard-prov" title="The run was not verified by the server, so this conclusion is provisional.">{provisional === status ? "unverified" : `agent: ${STATUS[provisional].short} · unverified`}</span>}
        <span className="dcard-ver">
          <DocVersion version={doc.version} />
        </span>
      </span>
      <span className="dcard-title">{doc.title}</span>
      {!compact && <span className="dcard-text">{doc.content}</span>}
      <span className="dcard-foot">
        <code>{doc.id}</code>
        {live ? (
          <span className={`dcard-live live-${live}`}>{LIVE_LABEL[live]}</span>
        ) : open.length ? (
          <span className="dcard-open">{open[0]!.status === "approved" ? "approved · not applied" : "correction to review"}</span>
        ) : null}
      </span>
    </button>
  );
}

export function ImpactBoard({ snapshot, filter, selectedId = null, onSelect, live = new Map(), analysing = false, compact = false }: Props) {
  const lanes = buildLanes(snapshot, filter);
  const c = consistency(snapshot);
  const { policy } = snapshot;
  return (
    <div className={`board${compact ? " board-compact" : ""}`}>
      <div className={`board-policy${analysing ? " is-analysing" : ""}`}>
        <div className="bp-head">
          <PolicyVersion version={policy.version} current />
          <code className="bp-id">{policy.id}</code>
        </div>
        <p className="bp-fact">
          <strong>{policy.voluntary_return_days} days</strong> voluntary returns
        </p>
        <p className="bp-sub">for purchases from {formatDate(policy.effective_from)}</p>
        <dl className="bp-stats">
          <div>
            <dt>Docs</dt>
            <dd>{c.total}</dd>
          </div>
          <div>
            <dt>Drifted</dt>
            <dd className={c.drifted ? "is-bad" : ""}>{c.drifted}</dd>
          </div>
          <div>
            <dt>Consistent</dt>
            <dd>{c.consistent}</dd>
          </div>
        </dl>
        {analysing && (
          <p className="bp-analysing" role="status">
            <span className="pulse" aria-hidden /> Agent run in progress
          </p>
        )}
      </div>
      <div className="lanes" role="list" aria-label="Channels">
        {lanes.length === 0 && <p className="muted lanes-empty">No documents match the current filter.</p>}
        {lanes.map((lane) => {
          const Icon = lane.channel.audience === "public" ? Globe : Lock;
          return (
            <section className="lane" key={lane.channel.id} role="listitem" aria-label={lane.channel.name}>
              <header className="lane-head">
                <span className="lane-name">
                  <Icon size={13} aria-hidden className="lane-icon" />
                  {lane.channel.name}
                </span>
                <span className="lane-meta">
                  {lane.channel.audience} · {lane.docs.length === lane.total ? lane.total : `${lane.docs.length} of ${lane.total}`}{" "}
                  {lane.total === 1 ? "document" : "documents"}
                </span>
              </header>
              <div className="lane-docs">
                {lane.docs.map((d) => (
                  <DocCard key={d.id} doc={d} selected={selectedId === d.id} live={live.get(d.id)} compact={compact} onSelect={onSelect} />
                ))}
              </div>
            </section>
          );
        })}
      </div>
    </div>
  );
}
