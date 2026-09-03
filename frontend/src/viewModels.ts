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
  drafts: Array<{ draft_id: string; version: number; body: string; source_refs: DailyReportDraft["source_refs"] }>;
  submissions: Array<{
    submission_id: string;
    version: number;
    body: string;
    source_refs: DailyReportDraft["source_refs"];
    reason: string | null;
    submitted_at: string;
  }>;
};
