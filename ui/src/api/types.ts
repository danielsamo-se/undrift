export type Category =
  | "OUTDATED"
  | "VALID_DISTINCT"
  | "EXISTING_COMMITMENT"
  | "UNRELATED"
  | "UNKNOWN";

export type ProposalStatus = "pending_review" | "approved" | "rejected" | "applied";

export interface EvaluationRun {
  slot: number; root_run_id: string; thread_id: string; status: string; reconciled: boolean; evidence_kind: string;
  database: string; database_id: string; phase_run_ids: string[]; message_ids: string[]; message_count: number;
  metrics: Record<string, number | null>; observed_unreconciled_metrics: Record<string, number | null> | null;
  native_question_count: number; answered_count: number; native_question_integrity: boolean;
  protected_automation_safe: boolean; mcp_calls: Record<string, number>; mcp_error_count: number;
  search_queries: string[]; reported_cost: string | null; duration_s: number;
  reserved_parent_steps: number; observed_llm_rounds: number; proposal_count: number;
  errors: string[]; misclassified_document_ids: string[]; final_retrieval_complete: boolean;
}
export interface EvaluationStatistic {
  minimum: number | null; mean: number | null; maximum: number | null; defined_count: number; result_count: number;
}
export interface ReliabilityReport {
  series_id?: string;
  active_series_id?: string;
  available_series?: string[];
  archived?: boolean;
  attempted_paid_messages?: number;
  diagnosis?: { summary: string; provider_native_question_count: number; persisted_question_count: number;
    provider_native_emission_recall: number; failed_rule: string; catalog_event_seq: number; first_document_retrieval_seq: number };
  version: string; state: string; stop_reason?: string | null; evidence_kind?: string;
  protocol_sha256?: string; cumulative_reported_cost?: string; protected_historical_files_unchanged?: boolean;
  results: EvaluationRun[];
  aggregate: { planned_conversations: number; recorded_conversations: number; not_started: number;
    reconciled_conversations: number; message_count: number; reported_total_cost: string;
    statistics: Record<string, EvaluationStatistic>;
    cost_statistics?: { minimum: string | null; mean: string | null; maximum: string | null; defined_count: number } };
}

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
  action?: "needs_clarification" | "manual_only" | "needs_correction" | "proposed" | "no_change" | null;
  id?: string;
  run_status?: string;
  verified_conclusion?: boolean;
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
  /** Applied by the bounded local finalizer under channel delegation; never a human approval. */
  delegated_application?: boolean;
}

export interface ImpactDocument {
  id: string;
  channel: string;
  title: string;
  content: string;
  version: number;
  updated_at: string;
  finding: Finding | null;
  provisional_findings?: { id: string; run_id: string; run_status: string; category: Category; evidence: string; reason: string }[];
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
  conversation_id?: string | null;
  awaiting_clarification?: boolean;
  document_outcomes?: Record<string, { action: string; proposal_id?: string }>;
  id: string;
  status: "open" | "completed" | "incomplete" | "failed" | "expired" | "interrupted";
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

export interface NativeQuestion {
  id: string;
  document_id: string;
  document_version: number;
  policy_version: number;
  prompt: string;
  options: string[];
  evidence: string;
  answer: string | null;
  answered_by: string | null;
}

export interface Conversation {
  continuation_blocker?: string | null;
  reported_total_cost?: string | null;
  id: string;
  thread_id: string | null;
  state: string;
  max_turns: number;
  max_total_steps: number;
  max_reported_cost: string;
  phases: { run_id: string; ordinal: number; message_id: string | null; state: string; cost: Cost | null; questions: NativeQuestion[] }[];
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
  active_run_ids?: string[];
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
  run_id?: string | null;
  finding_id?: string | null;
  evidence?: string;
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
  autonomy?: AutonomyDecision;
  evidence?: string;
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

export type GuardDecision = 'ALLOW' | 'NEEDS_REVIEW' | 'BLOCK';
export type GuardSource = 'cominty_guard' | 'deterministic_precheck' | 'fail_closed' | 'channel_boundary' | 'test_fixture';
export interface GuardFinding { claim: string | null; assessment: string | null; evidence_refs: string[]; reason: string | null }
export interface GuardCheck {
  id: string; database_id: string; created_at: string; requested_by: string;
  input_contract: string; output_contract: string; channel_id: string;
  candidate_sha256: string; candidate_length: number; candidate_redacted: string; case_sha256: string;
  policy_id: string; policy_version: number;
  evidence: { ref: string; document_id: string; channel_id: string; version: number; sha256: string }[];
  facts: { ref: string; sha256: string; statement_redacted: string }[];
  precheck: { verdict: 'CLEAR' | 'NEEDS_REVIEW' | 'BLOCK'; findings: { rule: string; verdict: string; sentence: string; reason: string }[] };
  execution: { mode: string; status: string; source: string; error: string | null; agent_id: string | null; thread_id: string | null; message_id: string | null;
    duration_s?: number | null; models?: string[] | null; tool_calls?: string[] | null;
    cost?: { total?: string | null; input_tokens?: number; output_tokens?: number; failed?: boolean } | null };
  agent_output: { verdict: string | null; findings: GuardFinding[]; uncertainty: string | null } | null;
  verification: { ok: boolean; problems: string[]; stale: string[] };
  decision: GuardDecision; decision_source: GuardSource; release_eligible: boolean; reasons: string[];
}
export interface GuardOverview {
  executor: { mode: 'disabled' | 'cominty' | string; paid: boolean; agent_configured: boolean; input_contract: string; output_contract: string };
  journal_configured: boolean; checks: GuardCheck[]; sends_or_publishes: false;
}

export type MemoryStatus = 'authoritative' | 'historical' | 'unverified';
export interface MemoryMatch {
  path: string; version: string | null; status: MemoryStatus; verdict: string | null;
  policy: { id: string; version: number; sha256: string }; source_check_id: string | null; message_id: string | null;
  saved_by: string | null; saved_at: string | null;
}
export interface MemoryRecall { id: string; check_id: string; namespace: string; status: 'ok' | 'error'; matches: MemoryMatch[]; error: string | null; created_at: string }
export interface MemoryRecord {
  contract: string; kind: 'verified_interpretation' | 'reviewer_fact';
  policy: { id: string; version: number; sha256: string }; verdict?: string; statement?: string; candidate_redacted?: string;
  claims?: { claim: string; assessment: string; evidence_refs: string[]; reason: string }[];
  source: { guard_check_id?: string; message_id?: string; thread_id?: string; agent_id?: string; database_id?: string };
  saved_by: string; saved_at: string;
}
export interface MemoryFile {
  path: string; purpose: string | null; version: string | null; created_at: string | null; updated_at: string | null;
  status: MemoryStatus; record: MemoryRecord | null; written_by_this_service: boolean; changed_since_write: boolean;
}
export interface MemoryWrite { id: string; created_at: string; actor: string; namespace: string; path: string; kind: string; policy_version: number; cominty_version: string; source_check_id: string | null }
export interface MemorySnapshot { live: boolean; namespace: string; files: MemoryFile[]; writes: MemoryWrite[]; error: string | null; journal_configured: boolean }
export interface NotificationDelivery {
  id: string; created_at: string; key: string; kind: string; subject_id: string; phase: 'started' | 'delivered' | 'unconfirmed' | 'failed';
  actor: string; channel: string; text: string; error: string | null;
  execution: { mode: string; attempt_id?: string; message_id?: string; duration_s?: number; cost?: { total?: string } | null;
    slack?: { message_ts?: string; permalink?: string; channel?: string } | null; tool_calls?: { name: string; status: string }[] };
}
export interface NotificationsSnapshot {
  mode: string; paid: boolean; connection: string; agent_configured: boolean; channel: string | null;
  connector: { name: string; title?: string; is_enabled?: boolean; requires_oauth_connect?: boolean; connection_status?: string | null; missing?: boolean; alternatives?: string[] } | null;
  connector_error: string | null; journal_configured: boolean; grants_approval: false;
  candidates: { key: string; kind: string; subject_id: string; text: string; last_phase: string | null }[];
  deliveries: NotificationDelivery[];
}

export type AutonomyMode = 'review_required' | 'bounded_auto' | 'pull_request' | 'manual_only';
export interface AutonomyPolicy extends Channel {
  channel_id: string; version: number; mode: AutonomyMode; effective_mode: AutonomyMode;
  max_per_run: number; allowed_modes: AutonomyMode[]; schema_available: boolean;
  persisted: boolean; configuration_valid: boolean; updated_at: string | null;
  updated_by: string | null; note: string;
}
export interface AutonomySnapshot {
  channels: AutonomyPolicy[]; global_run_limit: number; scope: string; historical: boolean;
}
export interface AutonomyDecision {
  mode: AutonomyMode; policy_version?: number; channel_id?: string; eligible: boolean;
  blockers: string[]; can_apply_locally: boolean; future_runs_only?: boolean;
  receipt: null | { proposal_id: string; run_id: string; document_id: string; channel_id: string;
    policy_version: number; original_sha256: string; proposed_sha256: string; applied_version: number; applied_at: string };
}

export interface Publication {
  status: "approved" | "pr_created" | "merged" | "deployed" | "verified" | "blocked";
  digest: string;
  target: { repository: string; branch: string; path: string; site_url: string };
  pr?: { number: number; url: string; commit: string };
  merge_commit?: string;
  error?: string | null;
  verified_at?: string;
  checked_at?: string;
  verification_evidence?: {
    source: "production_site";
    site_url: string;
    checked_at: string;
    expected_merge_commit: string;
    observed_build_commit: string;
    approved_content_sha256: string;
    raw_policy_sha256: string;
    rendered_policy_sha256: string;
    returns_page_sha256: string;
    policy_element_count: number;
    http_statuses: Record<string, number>;
  };
  events: { seq: number; ts: string; type: string; metadata: Record<string, unknown> }[];
}

export interface PublicationPlan {
  digest: string;
  target: Publication["target"];
  base_commit: string;
  original_content: string;
  proposed_content: string;
}
