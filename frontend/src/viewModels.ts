export type ProductSurface = "today" | "work" | "inbox" | "report" | "org";

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
  created_at?: string;
  updated_at?: string;
};

type AcceptedAssignment = {
  assignment_id: string;
  title: string;
  state: string;
};

export type MyWorkItem = DirectTask | AcceptedAssignment;

export const isDirectTask = (item: MyWorkItem): item is DirectTask => "task_id" in item;

export type OrganizationProfile = {
  member_id: string;
  display_name: string;
  organizations: Array<{ id: string; name: string }>;
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
  title: string;
  requester_id?: string;
  assignee_id?: string;
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
