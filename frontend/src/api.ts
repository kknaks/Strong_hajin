import type {
  ActionItemDetail,
  CalendarEntry,
  MeetingDetail,
  MeetingNote,
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

export async function getDeveloperPersonas(): Promise<Persona[]> {
  return request<Persona[]>("/api/developer/personas");
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
  extra: { description?: string; start_date?: string | null; due_date?: string | null; checklist?: string[]; reference_task_ids?: string[] } = {},
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
  return request<DirectTask>(`/api/tasks/${taskId}`, { body: JSON.stringify(body), method: "PATCH" });
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

export async function detachTaskMaterial(taskId: string, materialId: string): Promise<TaskMaterial> {
  return request<TaskMaterial>(`/api/tasks/${taskId}/materials/${materialId}/detach`, { method: "POST" });
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
): Promise<ConversationMessageAcceptance> {
  return request<ConversationMessageAcceptance>(`/api/conversations/${conversationId}/messages`, {
    body: JSON.stringify({ body, context }),
    headers: { "Idempotency-Key": idempotencyKey },
    method: "POST",
  });
}

export async function decideAction(
  actionId: string,
  expectedVersion: number,
  decision: "approve" | "reject",
): Promise<void> {
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
  developer: boolean;
  oidc: boolean;
  accounts: Persona[];
};

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

export async function login(account: string): Promise<OrganizationProfile> {
  return request<OrganizationProfile>("/api/auth/login", {
    body: JSON.stringify({ provider: "developer", account }),
    method: "POST",
  });
}

export async function logout(): Promise<void> {
  await request<void>("/api/auth/logout", { method: "POST" });
}

export async function getOrganizationTree(): Promise<OrganizationUnitNode[]> {
  return request<OrganizationUnitNode[]>("/api/organization/tree");
}

export async function getOrganizationUnitMembers(unitId: string): Promise<OrganizationMember[]> {
  return request<OrganizationMember[]>(`/api/organization/units/${unitId}/members`);
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
  payload: { expected_version?: number | null; reason?: string; changes?: Record<string, unknown> } = {},
): Promise<ActionItemEnvelope> {
  return request<ActionItemEnvelope>(`/api/action-items/${actionItemId}/commands/${command}`, {
    body: JSON.stringify(payload),
    method: "POST",
  });
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
