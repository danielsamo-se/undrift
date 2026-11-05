export type Category =
  | "OUTDATED"
  | "VALID_DISTINCT"
  | "EXISTING_COMMITMENT"
  | "UNRELATED"
  | "UNKNOWN";

export type ProposalStatus = "pending_review" | "approved" | "rejected" | "applied";

export interface Policy {
  id: string;
  version: number;
  policy_type: string;
  voluntary_return_days: number;
  effective_from: string;
  description: string;
}

export interface PolicyVersion extends Policy {
  created_at: string;
  created_by: string;
}

export interface Channel {
  id: string;
  name: string;
  audience: "public" | "internal";
}

export interface Finding {
  category: Category;
  classified_at: string;
  run_id: string | null;
  thread_id: string | null;
  agent_id: string | null;
  document_version: number | null;
  policy_version: number | null;
  evidence: string | null;
  evidence_verified: boolean;
  reason: string | null;
  prefilter: "actionable" | "protected" | "not_stale" | null;
  agrees_with_prefilter: boolean | null;
  current: boolean;
}

export interface Prefilter {
  state: "actionable" | "protected" | "not_stale";
  stale_sentences: number[];
  protected: Record<string, string>;
}

export interface ProposalSummary {
  id: string;
  status: ProposalStatus;
  base_version: number;
  policy_version: number;
  created_at: string;
  created_by: string;
}

export interface ImpactDocument {
  id: string;
  channel: string;
  title: string;
  content: string;
  version: number;
  updated_at: string;
  finding: Finding | null;
  prefilter: Prefilter;
  proposals: ProposalSummary[];
}

export interface Retrieval {
  policy_versions: number[];
  document_versions: Record<string, number[]>;
  missing: string[];
  complete: boolean;
}

export interface Cost {
  total?: string;
  input_tokens?: number;
  output_tokens?: number;
  cached_tokens?: number;
}

export interface McpCall {
  seq: number;
  ts: string;
  status: "ok" | "error" | "denied";
  tool: string | null;
  document_id: string | null;
  policy_id: string | null;
  version: number | null;
  secret_slot: string | null;
  user_agent: string | null;
  duration_ms: number | null;
  error: string | null;
}

export interface TraceStep {
  kind: "llm" | "tool_call";
  status: string;
  tool: string | null;
  model: string | null;
  description: string;
}

export interface RunSummary {
  id: string;
  status: "running" | "success" | "error" | string;
  started_at: string;
  finished_at: string | null;
  duration_s: number | null;
  agent_id: string | null;
  thread_id: string | null;
  message_id: string | null;
  cost: Cost | null;
  retrieval: Retrieval | null;
  mcp_calls: McpCall[];
  classifications: Record<string, Category>;
  proposals: string[];
  errors: string[];
  event_range: [number, number];
  record_file: string | null;
}

export interface Verdict {
  policy?: { id?: string; version?: number | null; voluntary_return_days?: number | null };
  documents?: Record<string, { category?: string; document_version?: number | null; evidence?: string | null; reason?: string }>;
  changes_made?: boolean;
}

export interface RunDetail extends RunSummary {
  verdict: Verdict | null;
  explanation: string | null;
  trace: TraceStep[];
}

export interface ImpactSnapshot {
  policy: Policy;
  policy_title: string;
  policy_versions: PolicyVersion[];
  activity: { events: number; mcp_calls: number; latest_seq: number };
  runs_total: number;
  channels: Channel[];
  documents: ImpactDocument[];
  latest_run: RunSummary | null;
}

export interface ActivityEvent {
  seq: number;
  ts: string;
  type: string;
  actor: string;
  status: "ok" | "error" | "denied";
  subject_type: string | null;
  subject_id: string | null;
  metadata: Record<string, unknown>;
  error: string | null;
}

export interface EventsPage {
  events: ActivityEvent[];
  next_after: number;
  latest_seq: number;
}

export interface Proposal {
  id: string;
  document_id: string;
  base_version: number;
  policy_id: string;
  policy_version: number;
  original_content: string;
  proposed_content: string;
  diff: string;
  reason: string;
  status: ProposalStatus;
  created_at: string;
  created_by: string;
  provenance: Record<string, string>;
  reviewed_by: string | null;
  reviewed_at: string | null;
  review_note: string | null;
  applied_version: number | null;
  applied_at: string | null;
}

export interface ReviewFlag {
  flag: string;
  sentence?: number;
}

export interface ProposalDetail {
  proposal: Proposal;
  events: ActivityEvent[];
  review_flags: ReviewFlag[];
  checks: {
    document_current_version: number | null;
    policy_current_version: number | null;
    document_unchanged: boolean;
    policy_current: boolean;
  };
}
