import {
  Bot,
  Check,
  ChevronRight,
  CircleExclamationPoint,
  Copy,
  Database,
  FileCheck,
  FilePlus,
  FileX,
  Plug,
  Scale,
  Search,
  Send,
  Server,
  Shield,
  TriangleExclamationPoint,
  X,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { STATUS, type MapStatus } from "../lib/classification";
import type { IconName } from "../lib/events";

export const EVENT_ICONS: Record<IconName, LucideIcon> = {
  plug: Plug,
  bot: Bot,
  scale: Scale,
  "file-check": FileCheck,
  "file-x": FileX,
  "file-plus": FilePlus,
  shield: Shield,
  server: Server,
  database: Database,
  search: Search,
  check: Check,
  x: X,
  send: Send,
  alert: TriangleExclamationPoint,
};

export function StatusDot({ status }: { status: MapStatus }) {
  return <span className={`dot dot-${status}`} aria-hidden />;
}

export function Status({ status, stale = false, short = false }: { status: MapStatus; stale?: boolean; short?: boolean }) {
  return (
    <span className={`status status-${status}`} title={STATUS[status].description}>
      <StatusDot status={status} />
      {short ? STATUS[status].short : STATUS[status].label}
      {stale && (
        <span className="stale" title="Analysed against an older document or policy version">
          stale
        </span>
      )}
    </span>
  );
}

const PROPOSAL_LABEL: Record<string, string> = {
  pending_review: "Needs review",
  approved: "Approved",
  rejected: "Rejected",
  applied: "Applied",
};

export function ProposalState({ status }: { status: string }) {
  return (
    <span className={`pstate pstate-${status}`}>
      <span className="pstate-mark" aria-hidden />
      {PROPOSAL_LABEL[status] ?? status}
    </span>
  );
}

/** Policy versions (P2) and published document versions (v3) use distinct, quiet tokens. */
export function PolicyVersion({ version, current = false }: { version: number | null | undefined; current?: boolean }) {
  return (
    <span className={`tok tok-policy${current ? " tok-current" : ""}`} title={`Policy version ${version ?? "?"}${current ? " (current)" : ""}`}>
      P{version ?? "?"}
    </span>
  );
}

export function DocVersion({ version }: { version: number | null | undefined }) {
  return (
    <span className="tok tok-doc" title={`Published document version ${version ?? "?"}`}>
      v{version ?? "?"}
    </span>
  );
}

export function Section({ title, aside, children, className = "" }: {
  title?: ReactNode;
  aside?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`section ${className}`}>
      {(title || aside) && (
        <header className="section-head">
          {title && <h2>{title}</h2>}
          {aside && <div className="section-aside">{aside}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function Disclosure({ summary, children, defaultOpen = false }: { summary: ReactNode; children: ReactNode; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className={`disclosure${open ? " is-open" : ""}`}>
      <button type="button" className="disclosure-btn" aria-expanded={open} onClick={() => setOpen(!open)}>
        <ChevronRight size={14} aria-hidden className="disclosure-chev" />
        {summary}
      </button>
      {open && <div className="disclosure-body">{children}</div>}
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <p className="empty-title">{title}</p>
      {children && <div className="empty-body">{children}</div>}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: Error; onRetry?: () => void }) {
  return (
    <div className="error-state" role="alert">
      <CircleExclamationPoint size={16} aria-hidden />
      <div>
        <p className="error-title">Could not load data</p>
        <p className="error-body">{error.message}</p>
      </div>
      {onRetry && (
        <button className="btn btn-sm" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}

export function LoadingBlock({ rows = 4 }: { rows?: number }) {
  return (
    <div className="loading" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => (
        <span key={i} className="skeleton" style={{ width: `${94 - i * 12}%` }} />
      ))}
    </div>
  );
}

export function Spinner({ size = 12 }: { size?: number }) {
  return <span className="spinner" style={{ width: size, height: size }} aria-hidden />;
}

export function CopyText({ value, label, display }: { value: string; label?: string; display?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className="copy"
      title={`Copy ${label ?? "value"}: ${value}`}
      onClick={() => {
        void navigator.clipboard?.writeText(value).then(() => {
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1200);
        });
      }}
    >
      <code>{display ?? value}</code>
      {copied ? <Check size={11} aria-hidden /> : <Copy size={11} aria-hidden />}
    </button>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="kbd">{children}</kbd>;
}

export function Modal({ title, onClose, children, footer, tone = "default" }: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  tone?: "default" | "danger";
}) {
  const id = useId();
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.querySelector<HTMLElement>("input, textarea, button:not(.icon-btn)")?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      prev?.focus();
    };
  }, [onClose]);
  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal modal-${tone}`} role="dialog" aria-modal="true" aria-labelledby={id} ref={ref}>
        <header className="modal-head">
          <h2 id={id}>{title}</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            <X size={15} />
          </button>
        </header>
        <div className="modal-body">{children}</div>
        {footer && <footer className="modal-foot">{footer}</footer>}
      </div>
    </div>
  );
}

/** Minimal, safe rendering of agent prose: **bold**, `code`, "- " bullets, paragraphs. Never HTML. */
export function AgentProse({ text }: { text: string }) {
  const inline = (s: string) =>
    s.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, i) =>
      part.startsWith("**") && part.endsWith("**") ? (
        <strong key={i}>{part.slice(2, -2)}</strong>
      ) : part.startsWith("`") && part.endsWith("`") ? (
        <code key={i}>{part.slice(1, -1)}</code>
      ) : (
        part
      ),
    );
  return (
    <div className="prose">
      {text.split(/\n{2,}/).map((block, i) => {
        const lines = block.split("\n");
        const bullets = lines.filter((l) => l.startsWith("- "));
        return (
          <div key={i}>
            {lines
              .filter((l) => !l.startsWith("- "))
              .map((l, j) => (
                <p key={j}>{inline(l)}</p>
              ))}
            {bullets.length > 0 && (
              <ul>
                {bullets.map((l, j) => (
                  <li key={j}>{inline(l.slice(2))}</li>
                ))}
              </ul>
            )}
          </div>
        );
      })}
    </div>
  );
}
