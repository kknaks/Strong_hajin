export type ProductSurface = "today" | "calendar" | "work" | "report" | "org";

export type Persona = {
  id: string;
  display_name: string;
};

export type TaskState = "open" | "in_progress" | "blocked" | "done" | "cancelled";

export type DirectTask = {
  task_id: string;
  title: string;
  state: TaskState;
  version: number;
  block_reason: string | null;
  description?: string | null;
  start_date?: string | null;
  due_date?: string | null;
  created_at?: string;
  updated_at?: string;
  organization_unit_id?: string | null;
  origin_kind?: string;
  visibility?: string;
  assignment?: TaskAssignmentSummary | null;
  lineage?: TaskLineage;
  /** Where this Task came from, decided and authorized by the server. Never derived on the client. */
  origin?: TaskOrigin | null;
  /** "owner" holds the task; "read_only" may look at it through a relationship they are allowed to read. */
  access?: "owner" | "read_only";
  /** Who holds the work right now, projected from the active assignment by the server. */
  assignee?: { member_id: string; display_name: string } | null;
  /** Steps inside this Task. Present on the detail read, not on list projections. */
  checklist?: ChecklistItem[];
  checklist_progress?: { done: number; total: number };
};

/**
 * The Task's provenance as the server resolved it. `actor_role` already says which role the actor played, so no
 * surface has to decide whether a member id means requester, assigner or assignee. `source` is absent when the
 * caller may not read the resource behind it.
 */
export type TaskOrigin = {
  kind: "self_created" | "work_request" | "direct_assignment" | string;
  actor_role: string;
  actor: { member_id: string; display_name: string } | null;
  source: { type: string; id: string; title: string | null } | null;
};

/** One step inside a Task: no assignment, no lineage, no judgement. */
export type ChecklistItem = {
  item_id: string;
  text: string;
  position: number;
  done: boolean;
  completed_by: string | null;
  completed_at: string | null;
};

export type TaskAssignmentSummary = {
  assignment_id: string;
  kind: "self" | "request_effect" | "direct";
  status: "pending" | "active" | "declined" | "superseded";
  assigned_by: string;
  accepted_at: string | null;
};

export type TaskAssignment = {
  assignment_id: string;
  assignment_kind: "self" | "request_effect" | "direct";
  status: "pending" | "active" | "declined" | "superseded";
  assignee_id: string;
  assigned_by: string;
  decline_reason: string | null;
  created_at: string;
  accepted_at: string | null;
  declined_at: string | null;
  task: DirectTask;
};

export type TaskLineage = {
  request_thread_id: string | null;
  source_work_request_id: string | null;
  source_decision_item_id: string | null;
  source_submission_id: string | null;
  source_review_decision_id: string | null;
  source_action_item_id: string | null;
  source_task_id: string | null;
};

export type TaskMaterialKind = "input" | "output";

export type TaskMaterial = {
  material_id: string;
  task_id: string;
  kind: TaskMaterialKind;
  name: string;
  content_type: string;
  size_bytes: number;
  uploaded_by: string;
  created_at: string;
  removed_at: string | null;
  extraction?: MaterialExtraction | null;
};

export type MaterialExtraction = {
  extraction_id: string;
  status: "queued" | "running" | "completed" | "failed" | "unsupported";
  extractor: string | null;
  failure_reason: string | null;
  failure_text: string | null;
  chunk_count: number;
  char_count: number;
  page_count: number | null;
  attempt_count: number;
  requested_at: string | null;
  completed_at: string | null;
};

export type MaterialEvidence = {
  evidence_id: string;
  turn_id: string;
  task_id: string;
  material_id: string;
  attachment_id: string;
  chunk_id: string;
  name: string;
  integrity_ref: string;
  page: number | null;
  excerpt: string;
  query: string;
  rank: number;
  origin: string;
  recorded_at: string;
};

export type TaskPatch = {
  title?: string;
  description?: string;
  start_date?: string | null;
  due_date?: string | null;
};


export type AccessGrant = {
  grant_id: string;
  role_id: string | null;
  role_label: string | null;
  capability_id: string | null;
  role_capability_version: number | null;
  scope_kind: string;
  scope_ref: string | null;
  scope_name: string | null;
  include_descendants: boolean;
  origin_rule_id: string | null;
  granted_by: string | null;
  valid_from: string;
  valid_until: string | null;
};

export type OrganizationProfile = {
  member_id: string;
  display_name: string;
  organizations: Array<{ id: string; name: string }>;
  roles?: string[];
  grants?: AccessGrant[];
  capabilities: string[];
};

export type DailyReportDraft = {
  report_id: string;
  draft_id: string;
  draft_version: number;
  body: string;
  source_refs: Array<{
    task_id: string;
    task_version: number;
    state: string;
    occurred_at: string;
  }>;
  status: string;
  workflow_run_id?: string;
  definition_version_id?: string;
  workflow_state?: string;
  submission_status?: string;
};

export type DailyReportHistory = {
  report_id: string;
  report_date: string;
  status: string;
  drafts: Array<{
    draft_id: string;
    version: number;
    body: string;
    source_refs: DailyReportDraft["source_refs"];
    workflow_run_id: string;
    definition_version_id: string;
  }>;
  submissions: Array<{
    submission_id: string;
    version: number;
    body: string;
    source_refs: DailyReportDraft["source_refs"];
    reason: string | null;
    submitted_at: string;
  }>;
};

export type DailyReportStatus = {
  report_date: string;
  status: "not_started" | "draft" | "submitted";
  report_id: string | null;
};

export type WorkRequest = {
  request_id: string;
  request_thread_id?: string | null;
  submission_version?: number | null;
  title: string;
  description?: string | null;
  due_date?: string | null;
  requester_id?: string;
  assignee_id?: string;
  cc_member_ids?: string[];
  state: "pending" | "negotiating" | "accepted" | "rejected" | "withdrawn";
  version: number;
  task_id: string | null;
  assignment_state: string | null;
  conditions: Record<string, unknown> | null;
};

export type ActionItem = {
  action_id: string;
  conversation_id: string;
  turn_id: string;
  action_type: string;
  title: string;
  state: "pending" | "approved" | "rejected";
  version: number;
  payload_summary: string;
  result: Record<string, unknown> | null;
  audit_ref: string | null;
  /** Server-provided approval controls; rendered verbatim, never inferred from state on the client. */
  commands?: ActionCommand[];
  /** Server-side presentation: the real work title, the operation kicker, and permission-safe preview rows. */
  subject?: string;
  operation_label?: string;
  preview?: ActionPreviewField[];
};

/** One independent judgement question, whatever raised it. The server decides everything the client renders. */
export type ActionItemEnvelope = {
  action_item_id: string;
  kind: string;
  status: "awaiting_review" | "awaiting_revision" | "resolved";
  subject: string;
  operation_label: string;
  current_question: string;
  preview: ActionPreviewField[];
  allowed_commands: ActionCommand[];
  submission_version: number;
  waiting_on: { member_id: string; display_name: string } | null;
  resource: { type: string; id: string };
  expected_version: number | null;
  /** The Task this proposal produced, when it produced one. */
  derived_task_id?: string | null;
  /** Fields the last reviewer asked to have changed. A proposal, never an edit: the round still holds what was sent. */
  suggested_changes?: Record<string, string>;
};

/** One immutable round: the frozen content that was judged, what changed since the previous round, and the answers. */
export type ActionRound = {
  submission_id: string;
  submission_version: number;
  submitted_by: string;
  submitted_at: string;
  content_hash: string;
  snapshot: Record<string, unknown>;
  diff: Record<string, { before: unknown; after: unknown }> | null;
  decisions: Array<{
    review_decision_id: string;
    actor_member_id: string;
    decision: string;
    reason: string | null;
    decided_at: string;
    suggested_changes?: Record<string, string>;
  }>;
};

/** One comment on the judgement. The discussion travels with the ActionItem and never moves it. */
export type ActionDiscussionEntry = {
  comment_id: string;
  author_member_id: string;
  body: string;
  created_at: string;
  edited_at: string | null;
  attachments: Array<{ attachment_id: string; name: string }>;
};

export type ActionItemDetail = ActionItemEnvelope & {
  rounds: ActionRound[];
  derived_task_id?: string | null;
  discussion?: ActionDiscussionEntry[];
};

export type ActionPreviewField = { id: string; label: string; value: string; kind: "text" | "date" | "person" | "people" | "state" | string };

export type ActionCommand = { id: string; label: string; tone: "primary" | "neutral" | "danger" | string; requires_reason?: boolean };

export type TurnProgressState = "queued" | "preparing" | "tool_running" | "composing" | "retrying" | "completed" | "failed" | "cancelled";

export type ConversationTurn = {
  turn_id: string;
  state: string;
  progress_state?: TurnProgressState;
  current_tool_display_name?: string | null;
  attempt?: number;
  queued_at?: string;
  execution_started_at?: string | null;
  execution_completed_at?: string | null;
  queue_wait_ms?: number | null;
  run_ms?: number | null;
  retry_of_turn_id?: string | null;
  usage?: Record<string, number> | null;
  provider_run_ref: string | null;
  provider_session_ref: string | null;
  error: string | null;
};

export type Conversation = {
  conversation_id: string;
  title: string;
  version: number;
  messages: Array<{
    message_id: string;
    turn_id: string | null;
    role: "user" | "assistant";
    body: string;
    sequence: number;
    state: "accepted" | "queued";
    body_state?: "final" | "streaming" | "failed" | "cancelled";
    idempotency_key?: string | null;
    created_at?: string;
  }>;
  turns: ConversationTurn[];
  context_references: ConversationContextReference[];
  tool_invocations: Array<{
    turn_id: string;
    sequence: number;
    provider_call_id: string | null;
    tool_name: string;
    display_name: string;
    input_summary: string;
    state: string;
    result_summary: string | null;
    error_summary: string | null;
    latency_ms: number | null;
    started_at?: string | null;
    completed_at?: string | null;
    target_resource_id: string | null;
    target_resource_version: string | null;
    audit_ref: string | null;
  }>;
  actions?: ActionItem[];
  material_evidence?: MaterialEvidence[];
};

export type ConversationContextReference = {
  resource_type: "task" | "work_request";
  resource_id: string;
  resource_version: number;
  included: boolean;
};

export type ConversationMessageAcceptance = {
  conversation_id: string;
  message_id: string;
  turn_id: string | null;
  queued: boolean;
  queue_size: number;
};

export type OrganizationUnitNode = {
  id: string;
  name: string;
  parent_id: string | null;
  unit_type: string | null;
  lifecycle: string;
  display_order: number;
  member_count: number;
  direct_member_count: number;
  leaders: Array<{ display_name: string; position: string; kind: string }>;
};

export type OrganizationMember = {
  member_id: string;
  display_name: string;
  memberships: Array<{ organization_id: string; organization_name: string; kind: string }>;
  positions: Array<{ position: string; organization_name: string; kind: string }>;
  grade: string | null;
  jobs: string[];
};

export type RequestAttachment = {
  attachment_id: string;
  name: string;
  content_type: string;
  size_bytes: number;
  uploaded_by: string;
  created_at: string;
};

export type RequestComment = {
  comment_id: string;
  author_member_id: string;
  body: string;
  created_at: string;
  edited_at: string | null;
  attachments?: RequestAttachment[];
};

export type RequestEvidence = {
  evidence_id: string;
  submission_id: string;
  submission_version: number;
  attachment_id: string;
  name: string;
  content_type: string;
  size_bytes: number;
  evidence_role: "supporting" | "decision_basis" | string;
  fixed_snapshot_ref: string;
  adopted_by: string;
  adopted_at: string;
  /** The request version adopting this produced: the basis moved, so an open reviewer's version is now stale. */
  request_version?: number;
};

/** One line of a round's basis: the attachment and its integrity, never the row that adopted it. */
export type EvidenceManifestEntry = { attachment_id: string; evidence_role: string; fixed_snapshot_ref: string };

export type RequestTimeline = {
  request: WorkRequest;
  request_thread_id: string | null;
  comments: RequestComment[];
  evidence?: RequestEvidence[];
  decision_item: { decision_item_id: string; kind: string; status: string; due_at: string | null } | null;
  submissions: Array<{
    submission_id: string;
    submission_version: number;
    revises_id: string | null;
    submitted_by: string;
    submitted_at: string;
    snapshot: Record<string, unknown>;
    subject_version: number | null;
    diff: Record<string, { before: unknown; after: unknown }> | null;
    /** What this round currently stands on, and the identity of that set. */
    evidence: EvidenceManifestEntry[];
    evidence_hash: string | null;
  }>;
  review_assignments: Array<{ review_assignment_id: string; submission_id: string; reviewer_member_id: string; status: string; assigned_at: string }>;
  review_decisions: Array<{ review_decision_id: string; submission_id: string; actor_member_id: string; decision: string; reason: string | null; conditions: Record<string, unknown> | null; evidence_hash: string | null; decided_at: string }>;
  activity: Array<{ event_kind: string; actor_id: string; safe_summary: string; reason: string | null; occurred_at: string }>;
};

/* ---- Meeting: the calendar entry SCAX owns, and the record it accumulates ---- */

/** A calendar row is either a meeting the caller may read, or a bare busy block that leaks nothing else. */
export type CalendarEntry = MeetingSummaryRow | { kind: "busy"; starts_at: string; ends_at: string };

export type MeetingSummaryRow = {
  kind: "meeting";
  meeting_id: string;
  organization_id: string;
  owner_id: string;
  title: string;
  starts_at: string;
  ends_at: string;
  visibility: "public" | "private";
  lifecycle: string;
  version: number;
  attendees: Array<{ member_id: string; display_name: string }>;
};

export type MeetingNoteVersion = {
  version_id: string;
  version: number;
  body: string;
  created_by: string;
  created_at: string;
  source_evidence: Array<Record<string, unknown>>;
};

export type MeetingNote = {
  note_id: string;
  lifecycle: string;
  version: number;
  body: string;
  versions: MeetingNoteVersion[];
  finalized_at: string | null;
  finalized_by: string | null;
};

/** Immutable provider output. Never edited; the refinement below is a projection of it. */
export type RawTranscriptSegment = {
  segment_id: string;
  source_segment_key: string;
  start_ms: number;
  end_ms: number;
  text: string;
  speaker_label: string | null;
  confirmed_member_id: string | null;
};

export type RefinedTranscriptSegment = RawTranscriptSegment & {
  raw_start_segment_id: string;
  raw_end_segment_id: string;
  correction_kind: string | null;
  confidence: number | null;
};

export type MeetingRecording = {
  recording_id: string;
  meeting_id: string;
  purpose: string;
  state: "not_started" | "recording" | "uploaded" | "transcribing" | "transcribed" | "failed" | "deleted" | string;
  version: number;
  content_type: string | null;
  original_name: string | null;
  size_bytes: number | null;
  sha256: string | null;
  started_at: string | null;
  ended_at: string | null;
  storage_key: null;
  raw_transcript: { transcript_revision_id: string; revision: number; state: string; provider: string; segments: RawTranscriptSegment[] } | null;
  refinement: { refinement_revision_id: string; raw_transcript_revision_id: string; revision: number; state: string; segments: RefinedTranscriptSegment[] } | null;
  speaker_assignments: Array<{ speaker_assignment_id: string; speaker_label: string; member_id: string; scope: string; state: string }>;
};

export type MeetingSummaryEvidence = {
  statement_index: number;
  kind: string;
  text: string;
  refinement_start_segment_id: string;
  refinement_end_segment_id: string;
  raw_start_segment_id: string;
  raw_end_segment_id: string;
  raw_start_ms: number;
  raw_end_ms: number;
};

export type MeetingSummary = {
  summary_id: string;
  meeting_id: string;
  raw_transcript_revision_id: string;
  refinement_revision_id: string;
  kind: "provisional" | "final" | string;
  state: "pending" | "adopted" | "dismissed" | "superseded" | "failed" | "completed" | string;
  version: number;
  body: string;
  evidence: MeetingSummaryEvidence[];
};

export type MeetingDetail = MeetingSummaryRow & {
  note: MeetingNote | null;
  recordings: MeetingRecording[];
  summaries: MeetingSummary[];
};
