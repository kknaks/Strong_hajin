import type {
  ActionMaterialDraft,
  AccessGrant,
  ActionItemDetail,
  CalendarEntry,
  MeetingDetail,
  MeetingNote,
  InstalledAccessRole,
  MemberAccess,
  MeetingRealtimeCredential,
  MeetingRecordingHandle,
  MeetingSummary,
  ChecklistItem,
  ActionItemEnvelope,
  DailyReportDraft,
  DailyReportHistory,
  DailyReportStatus,
  DirectTask,
  OrganizationMember,
  OrganizationUnitNode,
  RequestComment,
  RequestEvidence,
  RequestTimeline,
  TaskMaterial,
  TaskMaterialKind,
  TaskPatch,
  OrganizationProfile,
  Persona,
  TaskAssignment,
  ActionItem,
  WorkRequest,
  Conversation,
  ConversationContextReference,
  ConversationMessageAcceptance,
  TaskHistory,
  TaskHistoryDiff,
  TaskReference,
  GraphNode,
  GraphNeighborhood,
  GraphOverview,
  GraphView,
  Project,
  ProjectDetail,
  ProjectMember,
  Notification,
} from "./viewModels";

type ApiErrorBody = {
  detail?: unknown;
};

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      ...init.headers,
    },
  });

  if (!response.ok) {
    const error = (await response.json().catch(() => ({}))) as ApiErrorBody;
    const detail = typeof error.detail === "string" ? error.detail : response.statusText;
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

/** Names for ids, so the product can say who did what. Any signed-in member may read it; it opens nothing else. */
export async function getMemberDirectory(): Promise<Persona[]> {
  return request<Persona[]>("/api/organization/members");
}

export async function getMyWork(): Promise<DirectTask[]> {
  return request<DirectTask[]>("/api/my-work");
}

export async function getTasks(includeClosed = false): Promise<DirectTask[]> {
  return request<DirectTask[]>(includeClosed ? "/api/tasks?include_closed=true" : "/api/tasks");
}

export async function getTask(taskId: string): Promise<DirectTask> {
  return request<DirectTask>(`/api/tasks/${taskId}`);
}

/** Hand requested work back: what was delivered, and which of this Task's outputs it stands on. */
export async function submitTaskCompletion(
  taskId: string,
  expectedVersion: number,
  body: { summary: string; output_material_ids: string[] },
): Promise<DirectTask> {
  return request<DirectTask>(`/api/tasks/${taskId}/completion-report`, {
    body: JSON.stringify({ expected_version: expectedVersion, ...body }),
    method: "POST",
  });
}

export async function addTaskReference(taskId: string, referencedTaskId: string): Promise<TaskReference> {
  return request<TaskReference>(`/api/tasks/${taskId}/references`, {
    body: JSON.stringify({ referenced_task_id: referencedTaskId }),
    method: "POST",
  });
}

/** Stop pointing at it. The record keeps the pointer, so this is a release rather than a deletion. */
export async function releaseTaskReference(taskId: string, referenceId: string): Promise<{ reference_id: string; task_version: number }> {
  return request<{ reference_id: string; task_version: number }>(`/api/tasks/${taskId}/references/${referenceId}`, { method: "DELETE" });
}

/** Turn a followup a meeting produced into ordinary work. Pressing twice is a receipt, not a second Task. */
export async function promoteMeetingFollowup(
  meetingId: string,
  summaryId: string,
  statementIndex: number,
  body: { kind: "task" | "work_request"; title?: string; assignee_id?: string },
): Promise<{ already_promoted: boolean; task: DirectTask | null; work_request: WorkRequest | null }> {
  return request(`/api/meetings/${meetingId}/summaries/${summaryId}/statements/${statementIndex}/promote`, {
    body: JSON.stringify(body),
    method: "POST",
  });
}

/** The first screen of 관계 탐색: bounded, authorized, and already a graph. */
export async function graphOverview(view: GraphView = "member"): Promise<GraphOverview> {
  return request<GraphOverview>(`/api/graph/overview?view=${view}`);
}

export async function graphSearch(query: string, limit = 20): Promise<{ query: string; nodes: GraphNode[]; truncated: boolean }> {
  return request<{ query: string; nodes: GraphNode[]; truncated: boolean }>(
    `/api/graph/search?q=${encodeURIComponent(query)}&limit=${limit}`,
  );
}

export async function graphNeighbors(node: string, limit = 20): Promise<GraphNeighborhood> {
  return request<GraphNeighborhood>(`/api/graph/neighbors?node=${encodeURIComponent(node)}&limit=${limit}`);
}

export async function getTaskHistory(taskId: string): Promise<TaskHistory> {
  return request<TaskHistory>(`/api/tasks/${taskId}/history`);
}

export async function getTaskHistoryDiff(taskId: string, from: number, to: number): Promise<TaskHistoryDiff> {
  return request<TaskHistoryDiff>(`/api/tasks/${taskId}/history/diff?from=${from}&to=${to}`);
}

export async function getMyOrganizationProfile(): Promise<OrganizationProfile> {
  return request<OrganizationProfile>("/api/organization/me");
}

export async function createDirectTask(
  title: string,
  extra: {
    description?: string;
    start_date?: string | null;
    due_date?: string | null;
    checklist?: string[];
    reference_task_ids?: string[];
    parent_task_id?: string;
    project_id?: string;
  } = {},
): Promise<DirectTask> {
  return request<DirectTask>("/api/tasks", {
    body: JSON.stringify({ title, ...extra }),
    method: "POST",
  });
}

export async function updateTask(taskId: string, expectedVersion: number, patch: TaskPatch): Promise<DirectTask> {
  const body: Record<string, unknown> = { expected_version: expectedVersion };
  if (patch.title !== undefined) body.title = patch.title;
  if (patch.description !== undefined) body.description = patch.description;
  if (patch.start_date !== undefined) {
    if (patch.start_date) body.start_date = patch.start_date;
    else body.clear_start_date = true;
  }
  if (patch.due_date !== undefined) {
    if (patch.due_date) body.due_date = patch.due_date;
    else body.clear_due_date = true;
  }
  if (patch.project_id !== undefined) {
    if (patch.project_id) body.project_id = patch.project_id;
    else body.clear_project = true;
  }
  return request<DirectTask>(`/api/tasks/${taskId}`, { body: JSON.stringify(body), method: "PATCH" });
}

export async function getMaterialMetadata(materialId: string): Promise<{ material_id: string; name: string; origin: string; integrity_ref: string }> {
  return request(`/api/materials/${materialId}`);
}

export async function getTaskMaterials(taskId: string): Promise<TaskMaterial[]> {
  return request<TaskMaterial[]>(`/api/tasks/${taskId}/materials`);
}

export async function uploadTaskMaterial(taskId: string, kind: TaskMaterialKind, file: File): Promise<TaskMaterial> {
  const form = new FormData();
  form.append("kind", kind);
  form.append("file", file, file.name);
  const response = await fetch(`/api/tasks/${taskId}/materials`, { body: form, credentials: "same-origin", method: "POST" });
  if (!response.ok) {
    const error = (await response.json().catch(() => ({}))) as ApiErrorBody;
    throw new ApiError(response.status, typeof error.detail === "string" ? error.detail : response.statusText);
  }
  return response.json() as Promise<TaskMaterial>;
}

export function taskMaterialContentUrl(taskId: string, materialId: string): string {
  return `/api/tasks/${taskId}/materials/${materialId}/content`;
}

/** Point a Task at work that lives somewhere else. No bytes are held and no revision is pinned. */
export async function attachTaskMaterialLink(
  taskId: string,
  kind: TaskMaterialKind,
  link: { url: string; label: string },
): Promise<TaskMaterial> {
  return request<TaskMaterial>(`/api/tasks/${taskId}/materials/links`, {
    method: "POST",
    body: JSON.stringify({ kind, url: link.url, label: link.label }),
  });
}

/** Point a Task at another thing inside SCAX. What may be referenced is decided by the module that owns it. */
export async function attachTaskMaterialReference(
  taskId: string,
  kind: TaskMaterialKind,
  target: { resource_type: "task" | "meeting"; resource_id: string },
): Promise<TaskMaterial> {
  return request<TaskMaterial>(`/api/tasks/${taskId}/materials/references`, {
    method: "POST",
    body: JSON.stringify({ kind, ...target }),
  });
}

/** Move work that is already underway to someone else. Its own command, never a Task field edit. */
export async function reassignTask(
  taskId: string,
  expectedVersion: number,
  assigneeId: string,
  reason?: string,
): Promise<TaskAssignment> {
  return request<TaskAssignment>(`/api/tasks/${taskId}/reassign`, {
    method: "POST",
    body: JSON.stringify({ expected_version: expectedVersion, assignee_id: assigneeId, ...(reason ? { reason } : {}) }),
  });
}

export async function detachTaskMaterial(taskId: string, bindingId: string): Promise<TaskMaterial> {
  return request<TaskMaterial>(`/api/tasks/${taskId}/material-bindings/${bindingId}/detach`, { method: "POST" });
}

export async function transitionDirectTask(
  taskId: string,
  action: "start" | "block" | "resume" | "complete" | "cancel",
  expectedVersion: number,
  reason?: string,
): Promise<void> {
  await request(`/api/tasks/${taskId}/${action}`, {
    body: JSON.stringify({ expected_version: expectedVersion, ...(reason ? { reason } : {}) }),
    method: "POST",
  });
}

export async function generateDailyReportDraft(
  reportDate: string,
): Promise<DailyReportDraft> {
  return request<DailyReportDraft>("/api/daily-reports/generate-draft", {
    body: JSON.stringify({ report_date: reportDate }),
    method: "POST",
  });
}

export async function editDailyReport(
  reportId: string,
  draftId: string,
  expectedVersion: number,
  body: string,
): Promise<DailyReportDraft> {
  return request<DailyReportDraft>(`/api/daily-reports/${reportId}/edit`, {
    body: JSON.stringify({
      draft_id: draftId,
      expected_version: expectedVersion,
      body,
      include_source_refs: [],
      exclude_source_refs: [],
    }),
    method: "POST",
  });
}

export async function submitDailyReport(
  reportId: string,
  draftId: string,
  expectedVersion: number,
  reason?: string,
): Promise<void> {
  await request(`/api/daily-reports/${reportId}/submit`, {
    body: JSON.stringify({
      draft_id: draftId,
      expected_version: expectedVersion,
      reason: reason ?? null,
    }),
    method: "POST",
  });
}

export async function getDailyReportHistory(
  reportId: string,
): Promise<DailyReportHistory> {
  return request<DailyReportHistory>(`/api/daily-reports/${reportId}/history`);
}

export async function getDailyReportStatus(
  reportDate: string,
): Promise<DailyReportStatus> {
  const query = new URLSearchParams({ report_date: reportDate });
  return request<DailyReportStatus>(`/api/daily-reports/status?${query}`);
}

export async function getWorkRequests(): Promise<WorkRequest[]> {
  return request<WorkRequest[]>("/api/work-requests");
}

export async function getWorkRequest(requestId: string): Promise<WorkRequest> {
  return request<WorkRequest>(`/api/work-requests/${requestId}`);
}

export async function getActions(): Promise<ActionItem[]> {
  return request<ActionItem[]>("/api/actions");
}

export async function getWorkRequestAssigneeCandidates(): Promise<Persona[]> {
  return request<Persona[]>("/api/work-request-assignee-candidates");
}

export async function createWorkRequest(
  title: string,
  assigneeId: string,
  extra: { description?: string; due_date?: string | null; cc_member_ids?: string[]; checklist?: string[]; reference_task_ids?: string[] } = {},
): Promise<WorkRequest> {
  return request<WorkRequest>("/api/work-requests", {
    body: JSON.stringify({ title, assignee_id: assigneeId, ...extra }),
    method: "POST",
  });
}

export async function decideWorkRequest(
  requestId: string,
  action: "accept" | "reject",
  expectedVersion: number,
  reason?: string,
): Promise<WorkRequest> {
  return request<WorkRequest>(`/api/work-requests/${requestId}/${action}`, {
    body: JSON.stringify({ expected_version: expectedVersion, reason: reason ?? null }),
    method: "POST",
  });
}

export async function negotiateWorkRequest(
  requestId: string,
  expectedVersion: number,
  conditions: Record<string, string>,
): Promise<WorkRequest> {
  return request<WorkRequest>(`/api/work-requests/${requestId}/negotiate`, {
    body: JSON.stringify({ expected_version: expectedVersion, conditions }),
    method: "POST",
  });
}

export async function getConversations(): Promise<Conversation[]> {
  return request<Conversation[]>("/api/conversations");
}

export async function getNotifications(): Promise<Notification[]> {
  return request<Notification[]>("/api/notifications");
}

export async function markNotificationRead(notificationId: string): Promise<Notification> {
  return request<Notification>(`/api/notifications/${notificationId}/read`, { method: "POST", body: "{}" });
}

export async function getConversation(conversationId: string): Promise<Conversation> {
  return request<Conversation>(`/api/conversations/${conversationId}`);
}

export async function createConversation(title = "새 대화"): Promise<Conversation> {
  return request<Conversation>("/api/conversations", { body: JSON.stringify({ title }), method: "POST" });
}

export async function sendConversationMessage(
  conversationId: string,
  body: string,
  context: ConversationContextReference[],
  idempotencyKey: string,
  followUpCandidateId?: string,
): Promise<ConversationMessageAcceptance> {
  return request<ConversationMessageAcceptance>(`/api/conversations/${conversationId}/messages`, {
    body: JSON.stringify({ body, context, follow_up_candidate_id: followUpCandidateId }),
    headers: { "Idempotency-Key": idempotencyKey },
    method: "POST",
  });
}

export async function decideAction(
  actionId: string,
  expectedVersion: number,
  decision: string,
  payload: { base_submission_version?: number; draft?: Record<string, unknown> } = {},
): Promise<void> {
  if (decision === "confirm" || decision === "cancel_assignment") {
    await runActionCommand(actionId, decision, { expected_version: expectedVersion, ...payload });
    return;
  }
  await request(`/api/actions/${actionId}/decide`, {
    body: JSON.stringify({ expected_version: expectedVersion, decision }),
    method: "POST",
  });
}

export async function cancelConversation(
  conversationId: string,
  expectedVersion: number,
): Promise<void> {
  await request(`/api/conversations/${conversationId}/cancel`, {
    body: JSON.stringify({ expected_version: expectedVersion }),
    method: "POST",
  });
}

export type AuthProviders = {
  /** Email and password sign-in. */
  local: boolean;
  oidc: boolean;
  /** Local demo only: the accounts `reset-demo` installed, offered as a way to skip typing. */
  demo_accounts?: Array<{ member_id: string; email: string; display_name: string }>;
  demo_password?: string;
};

/** The roles this organization actually has, as they are now. Only for someone who administers access. */
export async function getInstalledAccessRoles(): Promise<InstalledAccessRole[]> {
  return request<InstalledAccessRole[]>("/api/access/roles");
}

export async function getMemberAccess(memberId: string): Promise<MemberAccess> {
  return request<MemberAccess>(`/api/access/members/${memberId}`);
}

/** Widen someone's authority, at a named scope, with a reason that is kept with the change. */
export async function grantAccessRole(input: {
  member_id: string;
  role_id: string;
  scope_kind: "unit" | "organization";
  scope_ref: string;
  include_descendants: boolean;
  reason: string;
}): Promise<{ grant_id: string }> {
  return request<{ grant_id: string }>("/api/access/grants", { body: JSON.stringify(input), method: "POST" });
}

export async function revokeAccessGrant(grantId: string, reason: string): Promise<{ grant_id: string }> {
  return request<{ grant_id: string }>(`/api/access/grants/${grantId}/revoke`, {
    body: JSON.stringify({ reason }),
    method: "POST",
  });
}

export async function getAuthProviders(): Promise<AuthProviders> {
  return request<AuthProviders>("/api/auth/providers");
}

export async function getSession(): Promise<OrganizationProfile | null> {
  try {
    return await request<OrganizationProfile>("/api/auth/me");
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) return null;
    throw error;
  }
}

export async function setAssistantCharacterPreference(
  characterKey: string,
  expectedVersion: number,
): Promise<{ character_key: string; version: number }> {
  return request("/api/profile/preferences/assistant-character", {
    body: JSON.stringify({ character_key: characterKey, expected_version: expectedVersion }),
    method: "PUT",
  });
}

export async function login(email: string, password: string): Promise<OrganizationProfile> {
  return request<OrganizationProfile>("/api/auth/login", {
    body: JSON.stringify({ email, password }),
    method: "POST",
  });
}

export async function logout(): Promise<void> {
  await request<void>("/api/auth/logout", { method: "POST" });
}

export async function listProjects(): Promise<Project[]> {
  return request<Project[]>("/api/projects");
}

export async function getProject(projectId: string): Promise<ProjectDetail> {
  return request<ProjectDetail>(`/api/projects/${projectId}`);
}

export async function createProject(body: {
  name: string;
  description?: string | null;
  starts_on?: string | null;
  ends_on?: string | null;
}): Promise<Project> {
  return request<Project>("/api/projects", { method: "POST", body: JSON.stringify(body) });
}

export async function assignToProject(projectId: string, body: { member_id: string; kind: "lead" | "member" }): Promise<ProjectMember> {
  return request<ProjectMember>(`/api/projects/${projectId}/members`, { method: "POST", body: JSON.stringify(body) });
}

export async function releaseFromProject(projectId: string, memberId: string): Promise<void> {
  await request<void>(`/api/projects/${projectId}/members/${memberId}`, { method: "DELETE" });
}

export async function getOrganizationTree(): Promise<OrganizationUnitNode[]> {
  return request<OrganizationUnitNode[]>("/api/organization/tree");
}

export async function getOrganizationUnitMembers(unitId: string): Promise<OrganizationMember[]> {
  return request<OrganizationMember[]>(`/api/organization/units/${unitId}/members`);
}

/**
 * 한 사람의 여섯 축(계층 · 소속 · 직책 · 직급 · 직무 · 권한)과 재직 · 계정 유무를 한 번에.
 *
 * 응답의 **모양은 누구에게나 같다** — 볼 자격이 없는 축은 자리를 남기고 값만 비운다(`phone`·`birth_date` 는
 * null, `grants`·`revoked_grants` 는 빈 배열). 그래서 화면은 모양으로 권한을 추측하지 않는다.
 */
export type OrganizationMemberAxes = {
  member_id: string;
  display_name: string;
  employment_state: string;
  employment_type: string | null;
  /** 들어올 문이 있는가. 권한이 아니라 로그인 계정의 유무다. */
  has_account: boolean;
  phone: string | null;
  birth_date: string | null;
  /** 회사에서 이 사람 자리까지 내려오는 길. */
  hierarchy_path: Array<{ unit_id: string; name: string; unit_type: string | null }>;
  memberships: Array<{ unit_id: string; unit_name: string; kind: string; valid_from: string; valid_until: string | null }>;
  appointments: Array<{
    unit_id: string;
    unit_name: string;
    position: string;
    role_id: string | null;
    kind: string;
    valid_from: string;
    valid_until: string | null;
  }>;
  grade: { id: string; name: string } | null;
  jobs: Array<{ id: string; name: string; kind: string | null }>;
  grants: AccessGrant[];
  /** 회수는 지우는 것이 아니다 — 지금 닿지 않는다는 사실과 언제 거두었는지가 함께 남는다. */
  revoked_grants: Array<{
    grant_id: string;
    role_id: string | null;
    role_label: string | null;
    scope_kind: string;
    scope_ref: string | null;
    scope_name: string | null;
    valid_from: string;
    revoked_at: string | null;
  }>;
};

/** 이력을 되짚을 수 있는 축. 계층은 여기 없다 — 계층은 소속이 지나온 길을 다시 그린 것이다. */
export type MemberHistoryAxis = "membership" | "appointment" | "grade" | "job" | "grant";

/** 한 축이 지나온 기간 하나. 지금 값도 여기 한 행으로 들어 있다 — 아직 끝나지 않은 기간이다. */
export type MemberHistoryEntry = {
  value: string | null;
  unit_name: string | null;
  kind: string | null;
  valid_from: string;
  valid_until: string | null;
  reason: string | null;
  actor: string | null;
};

/** 조직 축에서 무슨 일이 있었는가, 최신순. `axis` 는 서버가 정한 한국어 축 이름이다(권한 · 소속 · 직책 · 조직). */
export type OrganizationActivityEvent = {
  occurred_at: string;
  axis: string;
  event_kind: string;
  summary: string;
  reason: string | null;
  actor_id: string;
  actor_name: string;
  target_id: string;
  target_type: string;
  /**
   * 이 행 다음부터 읽는 자리표 — (시각, id) 복합 커서다. 「더 보기」는 이것을 **그대로** 되보낸다.
   *
   * 서버 정렬이 (시각, id) 라서 경계도 그 둘이어야 한다. 시각만 되보내면 같은 시각의 나머지 사건이
   * 다음 쪽에서 통째로 빠진다 (PR #2 F4). 옛 형식(시각만)도 서버가 받긴 하지만 그 건너뜀이 그대로 남는다.
   */
  cursor: string;
};

export async function getOrganizationMemberAxes(memberId: string): Promise<OrganizationMemberAxes> {
  return request<OrganizationMemberAxes>(`/api/organization/members/${memberId}`);
}

/** 본인이거나 그 사람을 관리할 수 있는 사람만 읽는다 — 그 밖에는 403 이다. */
export async function getOrganizationMemberHistory(memberId: string, axis: MemberHistoryAxis): Promise<MemberHistoryEntry[]> {
  return request<MemberHistoryEntry[]>(`/api/organization/members/${memberId}/history?axis=${axis}`);
}

/**
 * 변경 기록. 조직을 말하지 않으면 조직 전체를 물은 것이고, 그때 필요한 자격도 조직 전체에 대한 것이다.
 *
 * 다음 쪽은 마지막 행의 `occurred_at` 을 `cursor` 로 다시 물어 받는다 — 서버가 그 시각보다 앞선 것만 준다.
 */
export async function getOrganizationActivity(input: { unitId?: string | null; limit?: number; cursor?: string | null } = {}): Promise<
  OrganizationActivityEvent[]
> {
  const query = new URLSearchParams();
  if (input.unitId) query.set("unit_id", input.unitId);
  query.set("limit", String(input.limit ?? 50));
  if (input.cursor) query.set("cursor", input.cursor);
  return request<OrganizationActivityEvent[]>(`/api/organization/activity?${query.toString()}`);
}

export async function getWorkRequestTimeline(requestId: string): Promise<RequestTimeline> {
  return request<RequestTimeline>(`/api/work-requests/${requestId}/timeline`);
}

export async function addWorkRequestComment(requestId: string, body: string, idempotencyKey: string): Promise<RequestComment> {
  return request<RequestComment>(`/api/work-requests/${requestId}/comments`, {
    body: JSON.stringify({ body }),
    headers: { "Idempotency-Key": idempotencyKey },
    method: "POST",
  });
}

export async function resubmitWorkRequest(
  requestId: string,
  expectedVersion: number,
  changes: { title?: string; description?: string; due_date?: string | null },
): Promise<WorkRequest> {
  const body: Record<string, unknown> = { expected_version: expectedVersion };
  if (changes.title !== undefined) body.title = changes.title;
  if (changes.description !== undefined) body.description = changes.description;
  if (changes.due_date !== undefined) {
    if (changes.due_date) body.due_date = changes.due_date;
    else body.clear_due_date = true;
  }
  return request<WorkRequest>(`/api/work-requests/${requestId}/resubmit`, { body: JSON.stringify(body), method: "POST" });
}

export async function getTaskAssignmentCandidates(): Promise<Persona[]> {
  return request<Persona[]>("/api/task-assignment-candidates");
}

export async function assignTask(
  title: string,
  assigneeId: string,
  extra: { description?: string; start_date?: string; due_date?: string; checklist?: string[] } = {},
): Promise<TaskAssignment> {
  return request<TaskAssignment>("/api/tasks/assign", { body: JSON.stringify({ title, assignee_id: assigneeId, ...extra }), method: "POST" });
}

export async function getSentTaskAssignments(): Promise<TaskAssignment[]> {
  return request<TaskAssignment[]>("/api/task-assignments/sent");
}

export async function acceptTaskAssignment(assignmentId: string): Promise<TaskAssignment> {
  return request<TaskAssignment>(`/api/task-assignments/${assignmentId}/accept`, { method: "POST", body: "{}" });
}

export async function declineTaskAssignment(assignmentId: string, reason: string): Promise<TaskAssignment> {
  return request<TaskAssignment>(`/api/task-assignments/${assignmentId}/decline`, { method: "POST", body: JSON.stringify({ reason }) });
}

export async function getWorkRequestCcCandidates(): Promise<Persona[]> {
  return request<Persona[]>("/api/work-request-cc-candidates");
}

async function uploadFile<T>(path: string, file: File): Promise<T> {
  const form = new FormData();
  form.append("file", file, file.name);
  const response = await fetch(path, { body: form, credentials: "same-origin", method: "POST" });
  if (!response.ok) {
    const error = (await response.json().catch(() => ({}))) as ApiErrorBody;
    throw new ApiError(response.status, typeof error.detail === "string" ? error.detail : response.statusText);
  }
  return response.json() as Promise<T>;
}

export async function uploadCommentAttachment(requestId: string, commentId: string, file: File): Promise<RequestComment> {
  return uploadFile<RequestComment>(`/api/work-requests/${requestId}/comments/${commentId}/attachments`, file);
}

/** The requester improving their own request before anyone has judged it. Content only, never the assignee. */
export async function amendWorkRequest(
  requestId: string,
  expectedVersion: number,
  changes: { title?: string; description?: string; due_date?: string | null },
): Promise<WorkRequest> {
  const body: Record<string, unknown> = { expected_version: expectedVersion };
  if (changes.title !== undefined) body.title = changes.title;
  if (changes.description !== undefined) body.description = changes.description;
  if (changes.due_date === null) body.clear_due_date = true;
  else if (changes.due_date !== undefined) body.due_date = changes.due_date;
  return request(`/api/work-requests/${requestId}/amend`, { method: "POST", body: JSON.stringify(body) });
}

export async function uploadRequestEvidence(requestId: string, file: File): Promise<RequestEvidence> {
  return uploadFile<RequestEvidence>(`/api/work-requests/${requestId}/evidence`, file);
}

export function requestAttachmentUrl(requestId: string, attachmentId: string): string {
  return `/api/work-requests/${requestId}/attachments/${attachmentId}/content`;
}

export async function retryConversationTurn(conversationId: string, turnId: string): Promise<{ conversation_id: string; turn_id: string; retry_of_turn_id: string }> {
  return request(`/api/conversations/${conversationId}/turns/${turnId}/retry`, { method: "POST", body: "{}" });
}

export async function getActionItems(): Promise<ActionItemEnvelope[]> {
  return request<ActionItemEnvelope[]>("/api/action-items");
}

export async function getActionItem(actionItemId: string): Promise<ActionItemDetail> {
  return request<ActionItemDetail>(`/api/action-items/${actionItemId}`);
}

export async function runActionCommand(
  actionItemId: string,
  command: string,
  payload: {
    expected_version?: number | null;
    base_submission_version?: number;
    draft?: Record<string, unknown>;
    attachment_draft_ids?: string[];
    reason?: string;
    changes?: Record<string, unknown>;
  } = {},
): Promise<ActionItemEnvelope> {
  return request<ActionItemEnvelope>(`/api/action-items/${actionItemId}/commands/${command}`, {
    body: JSON.stringify(payload),
    method: "POST",
  });
}

export async function stageActionMaterialLink(
  actionItemId: string,
  link: { url: string; label: string },
): Promise<ActionMaterialDraft> {
  return request<ActionMaterialDraft>(`/api/action-items/${actionItemId}/material-drafts/links`, {
    method: "POST",
    body: JSON.stringify(link),
  });
}

export async function stageActionMaterialFile(actionItemId: string, file: File): Promise<ActionMaterialDraft> {
  const form = new FormData();
  form.append("file", file, file.name);
  const response = await fetch(`/api/action-items/${actionItemId}/material-drafts/files`, {
    body: form,
    credentials: "same-origin",
    method: "POST",
  });
  if (!response.ok) {
    const error = (await response.json().catch(() => ({}))) as ApiErrorBody;
    throw new ApiError(response.status, typeof error.detail === "string" ? error.detail : response.statusText);
  }
  return response.json() as Promise<ActionMaterialDraft>;
}

export async function discardActionMaterialDraft(actionItemId: string, materialDraftId: string): Promise<void> {
  await request(`/api/action-items/${actionItemId}/material-drafts/${materialDraftId}/discard`, { method: "POST" });
}

export async function addChecklistItem(taskId: string, text: string): Promise<ChecklistItem> {
  return request<ChecklistItem>(`/api/tasks/${taskId}/checklist`, { body: JSON.stringify({ text }), method: "POST" });
}

export async function updateChecklistItem(
  taskId: string,
  itemId: string,
  patch: { text?: string; done?: boolean; expected_version?: number },
): Promise<ChecklistItem> {
  return request<ChecklistItem>(`/api/tasks/${taskId}/checklist/${itemId}`, { body: JSON.stringify(patch), method: "PATCH" });
}

/** Taking a step off the list. The record keeps it, so this is an archive rather than a deletion. */
export async function removeChecklistItem(taskId: string, itemId: string, expectedVersion?: number): Promise<ChecklistItem> {
  const query = expectedVersion === undefined ? "" : `?expected_version=${expectedVersion}`;
  return request<ChecklistItem>(`/api/tasks/${taskId}/checklist/${itemId}${query}`, { method: "DELETE" });
}

/** The whole order, every step exactly once: one step nudged on its own could collide with another. */
export async function reorderChecklist(
  taskId: string,
  itemIds: string[],
): Promise<{ task_version: number; checklist: ChecklistItem[] }> {
  return request<{ task_version: number; checklist: ChecklistItem[] }>(`/api/tasks/${taskId}/checklist/order`, {
    body: JSON.stringify({ item_ids: itemIds }),
    method: "POST",
  });
}

export async function getCalendarEntries(): Promise<CalendarEntry[]> {
  return request<CalendarEntry[]>("/api/meetings");
}

export async function getMeeting(meetingId: string): Promise<MeetingDetail> {
  return request<MeetingDetail>(`/api/meetings/${meetingId}`);
}

export function meetingMaterialContentUrl(meetingId: string, materialId: string): string {
  return `/api/meetings/${meetingId}/materials/${materialId}/content`;
}

export async function createMeeting(input: {
  organization_id: string;
  title: string;
  starts_at: string;
  ends_at: string;
  visibility: "public" | "private";
  attendee_ids: string[];
}): Promise<MeetingDetail> {
  return request<MeetingDetail>("/api/meetings", { body: JSON.stringify(input), method: "POST" });
}

export async function createMeetingNote(meetingId: string, body: string): Promise<MeetingNote> {
  return request<MeetingNote>(`/api/meetings/${meetingId}/note`, { body: JSON.stringify({ body }), method: "POST" });
}

export async function saveMeetingNote(meetingId: string, expectedVersion: number, body: string): Promise<MeetingNote> {
  return request<MeetingNote>(`/api/meetings/${meetingId}/note`, {
    body: JSON.stringify({ expected_version: expectedVersion, body }),
    method: "PATCH",
  });
}

export async function finalizeMeetingNote(meetingId: string, expectedVersion: number): Promise<MeetingNote> {
  return request<MeetingNote>(`/api/meetings/${meetingId}/note/finalize`, {
    body: JSON.stringify({ expected_version: expectedVersion }),
    method: "POST",
  });
}

/** Open a recording. The server decides whether this person may record this meeting; the browser only asks. */
export async function startMeetingRecording(meetingId: string, purpose: string): Promise<MeetingRecordingHandle> {
  return request<MeetingRecordingHandle>(`/api/meetings/${meetingId}/recordings/start`, {
    body: JSON.stringify({ purpose }),
    method: "POST",
  });
}

/**
 * A restricted, short-lived key for this one recording's live stream.
 *
 * The long-lived provider key never reaches a browser: this returns a temporary key bound to this recording, so the
 * worst a captured page can do is stream into the session it was already allowed to open.
 */
export async function meetingRealtimeCredential(
  meetingId: string,
  recordingId: string,
  maxSessionDurationSeconds: number,
): Promise<MeetingRealtimeCredential> {
  return request<MeetingRealtimeCredential>(`/api/meetings/${meetingId}/recordings/${recordingId}/realtime-credential`, {
    body: JSON.stringify({ max_session_duration_seconds: maxSessionDurationSeconds }),
    method: "POST",
  });
}

/** Append what the live stream has settled on. Partial tokens are still changing, so they never come here. */
export async function appendMeetingRealtimeSegments(
  meetingId: string,
  recordingId: string,
  segments: Array<{ source_segment_key?: string | null; start_ms: number; end_ms: number; text: string; speaker_label?: string | null }>,
): Promise<{ transcript_revision_id: string; source_kind: string; segment_count: number }> {
  return request(`/api/meetings/${meetingId}/recordings/${recordingId}/realtime-segments`, {
    body: JSON.stringify({ segments }),
    method: "POST",
  });
}

/** Close the recording by handing over the audio itself; the authoritative reading is made from this file, not the stream. */
export async function stopMeetingRecording(
  meetingId: string,
  recordingId: string,
  expectedVersion: number,
  audio: Blob,
  fileName: string,
): Promise<MeetingRecordingHandle> {
  const form = new FormData();
  form.append("expected_version", String(expectedVersion));
  form.append("audio", audio, fileName);
  const response = await fetch(`/api/meetings/${meetingId}/recordings/${recordingId}/stop`, {
    body: form,
    credentials: "same-origin",
    method: "POST",
  });
  if (!response.ok) {
    const error = (await response.json().catch(() => ({}))) as ApiErrorBody;
    throw new ApiError(response.status, typeof error.detail === "string" ? error.detail : response.statusText);
  }
  return response.json() as Promise<MeetingRecordingHandle>;
}

export async function adoptMeetingSummary(
  meetingId: string,
  summaryId: string,
  expectedVersion: number,
): Promise<{ summary: MeetingSummary; note: MeetingNote }> {
  return request<{ summary: MeetingSummary; note: MeetingNote }>(`/api/meetings/${meetingId}/summaries/${summaryId}/adopt`, {
    body: JSON.stringify({ expected_version: expectedVersion }),
    method: "POST",
  });
}
