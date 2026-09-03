export type ProductSurface = "today" | "work" | "inbox" | "report" | "org";

export type Persona = {
  id: string;
  display_name: string;
};

export type TaskState = "active" | "in_progress" | "blocked" | "completed" | "cancelled";

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
