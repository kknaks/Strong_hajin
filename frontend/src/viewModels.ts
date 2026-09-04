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
  state: "pending" | "negotiating" | "accepted" | "rejected";
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
  }>;
  turns: Array<{ turn_id: string; state: string; provider_run_ref: string | null; provider_session_ref: string | null; error: string | null }>;
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
    target_resource_id: string | null;
    target_resource_version: string | null;
    audit_ref: string | null;
  }>;
  actions?: ActionItem[];
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
};

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
  }>;
  review_assignments: Array<{ review_assignment_id: string; submission_id: string; reviewer_member_id: string; status: string; assigned_at: string }>;
  review_decisions: Array<{ review_decision_id: string; submission_id: string; actor_member_id: string; decision: string; reason: string | null; conditions: Record<string, unknown> | null; decided_at: string }>;
  activity: Array<{ event_kind: string; actor_id: string; safe_summary: string; reason: string | null; occurred_at: string }>;
};
