import type {
  ActionMaterialDraft,
  AccessGrant,
  ActionItemDetail,
  InstalledAccessRole,
  MemberAccess,
  MeetingAgenda,
  MeetingLine,
  MeetingListPayload,
  MeetingMaterial,
  MeetingMaterialUpload,
  MeetingRecord,
  MeetingRoom,
  MeetingTodo,
  MeetingTranscript,
  MeetingViewer,
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
  TaskAssignmentsView,
  TaskChild,
  TaskProposalMutation,
  TaskProposalsView,
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
  ProjectParticipation,
} from "./viewModels";

type ApiErrorBody = {
  detail?: unknown;
};

export class ApiError extends Error {
  status: number;
  /** 서버가 문장 대신 구조를 낼 때가 있다 — 409 저장 충돌은 「지금 있는 것」을 함께 싣는다. */
  detail: unknown;
  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

export type BrowserInteraction = {
  interaction_id: string;
  kind: string;
  intent: string;
  target: { type: string; id: string; title: string; version: number | null };
  status: string;
  result: Record<string, unknown> | null;
  open_url: string;
  created_at: string;
  updated_at: string;
};

export async function getBrowserInteraction(id: string): Promise<BrowserInteraction> {
  return request<BrowserInteraction>(`/api/browser-interactions/${encodeURIComponent(id)}`);
}

export async function startBrowserRecording(id: string, captureId: string): Promise<BrowserInteraction> {
  return request<BrowserInteraction>(`/api/browser-interactions/${encodeURIComponent(id)}/recording/start`, {
    method: 'POST', body: JSON.stringify({ capture_id: captureId }),
  });
}

export async function stopBrowserRecording(id: string, captureId: string, audio: Blob): Promise<BrowserInteraction> {
  const form = new FormData();
  form.append('capture_id', captureId);
  form.append('file', audio, audio.type.includes('mp4') ? 'recording.mp4' : 'recording.webm');
  const response = await fetch(`/api/browser-interactions/${encodeURIComponent(id)}/recording/stop`, { method: 'POST', credentials: 'same-origin', body: form });
  if (!response.ok) {
    const error = (await response.json().catch(() => ({}))) as ApiErrorBody;
    throw new ApiError(response.status, typeof error.detail === 'string' ? error.detail : response.statusText);
  }
  return response.json() as Promise<BrowserInteraction>;
}

export async function interruptBrowserInteraction(id: string, status: 'cancelled' | 'denied' | 'failed' | 'unsupported'): Promise<BrowserInteraction> {
  return request<BrowserInteraction>(`/api/browser-interactions/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify({ status }) });
}

export async function uploadBrowserFile(id: string, file: File): Promise<BrowserInteraction> {
  const form = new FormData();
  form.append('file', file, file.name);
  const response = await fetch(`/api/browser-interactions/${encodeURIComponent(id)}/file`, { method: 'POST', credentials: 'same-origin', body: form });
  if (!response.ok) {
    const error = (await response.json().catch(() => ({}))) as ApiErrorBody;
    throw new ApiError(response.status, typeof error.detail === 'string' ? error.detail : response.statusText);
  }
  return response.json() as Promise<BrowserInteraction>;
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
    throw new ApiError(response.status, detail, error.detail);
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

/**
 * 업무 생성 — 본인 또는 수신자 지정 (W1 · WORK-001 Phase 1·6).
 *
 * `assignee_id` 가 없거나 본인이면 본인 업무, 다른 사람이면 **상대의 수락 없이** 그 사람의 활성 담당으로 선다.
 * 멱등 키는 **`Idempotency-Key` 헤더**로만 간다 — 본문 필드가 아니다(생성 입력 모델이 알 수 없는 필드를
 * 거부한다). 한 생성 의도에 키 하나이고, 재시도·연타는 같은 키를 다시 보낸다.
 */
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
    assignee_id?: string;
  },
  idempotencyKey: string,
): Promise<DirectTask> {
  return request<DirectTask>("/api/tasks", {
    body: JSON.stringify({ title, ...extra }),
    headers: { "Idempotency-Key": idempotencyKey },
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

/**
 * 담당 관계 — **현재와 대기를 각각** 낸다 (SPEC-003 §4 `GET /api/tasks/{id}/assignments` · P-3).
 *
 * 담당 변경 대기 동안 `active` 하나와 `pending` 하나가 함께 산다(V-18). 한 값으로 합쳐 읽으면
 * 화면에서 책임 공백이 생긴다.
 */
export async function getTaskAssignments(taskId: string): Promise<TaskAssignmentsView> {
  return request<TaskAssignmentsView>(`/api/tasks/${taskId}/assignments`);
}

/** 직속 하위만 (SPEC-003 §4 · L-11). 저장 깊이와 무관하고, 읽을 수 없는 하위는 들어 있지 않다. */
export async function getTaskChildren(taskId: string): Promise<TaskChild[]> {
  return request<TaskChild[]>(`/api/tasks/${taskId}/children`);
}

/**
 * 재개 (SPEC-003 §4 `POST /api/tasks/{id}/reopen`).
 *
 * **완료된 상위가 있으면 거부된다** — 상위를 먼저 재개하라고 답한다(L-13 · `WORK_REOPEN_PARENT_DONE`).
 * 이전 완료 이력·회차·결과는 보존된다.
 */
export async function reopenTask(taskId: string, expectedVersion: number, reason?: string): Promise<DirectTask> {
  return request<DirectTask>(`/api/tasks/${taskId}/reopen`, {
    body: JSON.stringify({ expected_version: expectedVersion, ...(reason ? { reason } : {}) }),
    method: "POST",
  });
}

/** 대기 중·지난 제안 (SPEC-003 §4 `GET /api/tasks/{id}/proposals`). 서버가 둘을 **각각** 낸다. */
export async function getTaskProposals(taskId: string): Promise<TaskProposalsView> {
  return request<TaskProposalsView>(`/api/tasks/${taskId}/proposals`);
}

/**
 * 수락 후의 제안 — 합의 취소와 조건 변경 (SPEC-003 §4 · V-19·V-20).
 *
 * **제안만으로는 아무것도 바뀌지 않는다.** 담당자가 동의해야 취소되고 조건이 움직인다.
 */
export async function createTaskProposal(
  taskId: string,
  expectedVersion: number,
  body: { kind: "cancellation" | "terms_change"; reason?: string; payload?: Record<string, unknown> },
): Promise<TaskProposalMutation> {
  return request<TaskProposalMutation>(`/api/tasks/${taskId}/proposals`, {
    body: JSON.stringify({ expected_version: expectedVersion, kind: body.kind, ...(body.reason ? { reason: body.reason } : {}), ...(body.payload ? { payload: body.payload } : {}) }),
    method: "POST",
  });
}

/** 동의 / 동의하지 않음 — **담당자만** 부른다 (`WORK_PROPOSAL_RESPONDER_ONLY`). */
export async function respondTaskProposal(
  taskId: string,
  proposalId: string,
  expectedVersion: number,
  body: { agree: boolean; reason?: string },
): Promise<TaskProposalMutation> {
  return request<TaskProposalMutation>(`/api/tasks/${taskId}/proposals/${proposalId}/respond`, {
    body: JSON.stringify({ expected_version: expectedVersion, agree: body.agree, ...(body.reason ? { reason: body.reason } : {}) }),
    method: "POST",
  });
}

/** 제안 철회 — 제안한 사람이 부른다. */
export async function withdrawTaskProposal(taskId: string, proposalId: string, expectedVersion: number): Promise<TaskProposalMutation> {
  return request<TaskProposalMutation>(`/api/tasks/${taskId}/proposals/${proposalId}/withdraw`, {
    body: JSON.stringify({ expected_version: expectedVersion }),
    method: "POST",
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
): Promise<DailyReportStatus> {
  return request<DailyReportStatus>("/api/daily-reports/generate-draft", {
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

/**
 * 내가 닿는 요청 전부.
 *
 * `includeRemoved` 는 **목록에서 정리(숨김)한 항목까지** 달라는 뜻이다 (L-6 · F-6) — 「숨긴 항목 보기」가
 * 새로고침 뒤에도 서는 길이다. 숨김 여부는 각 행의 `list_entry_hidden` 이 말한다.
 */
export async function getWorkRequests(includeRemoved = false): Promise<WorkRequest[]> {
  return request<WorkRequest[]>(includeRemoved ? "/api/work-requests?include_removed=true" : "/api/work-requests");
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

/**
 * 요청 발송 (SPEC-003 §4 `POST /api/work-requests`).
 *
 * 성공하면 `request`(`state=pending`)와 `open` Task 가 **한 덩어리로** 선다 — 담당은 아직 없고
 * `derived.assignment` 가 `awaiting_acceptance` 다(V-9·V-10). 멱등 키는 **헤더로만** 간다(K-1).
 *
 * `parent_task_id` 는 **이 입구에만 있다** — `POST /api/tasks` 의 수평 갈래는 그 값을 거절한다(O-27).
 * `supersedes_request_id` 는 「다시 요청」이 남기는 이전 요청 연결이다(V-12).
 */
export async function createWorkRequest(
  title: string,
  assigneeId: string,
  extra: {
    description?: string;
    due_date?: string | null;
    cc_member_ids?: string[];
    checklist?: string[];
    reference_task_ids?: string[];
    parent_task_id?: string;
    supersedes_request_id?: string;
  },
  idempotencyKey: string,
): Promise<WorkRequest> {
  return request<WorkRequest>("/api/work-requests", {
    body: JSON.stringify({ title, assignee_id: assigneeId, ...extra }),
    headers: { "Idempotency-Key": idempotencyKey },
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

/**
 * 수락 전 철회 (SPEC-003 §4). 요청은 `withdrawn`, 그 Task 는 `cancelled` 가 되고 상위 연결은 남는다.
 *
 * W1 까지 이것은 **판단함 명령으로만** 열려 있었다. v2 가 요청자의 일반 명령으로 올린다.
 */
export async function withdrawWorkRequest(requestId: string, expectedVersion: number): Promise<WorkRequest> {
  /* 서버 입력 모델은 **회차 하나만** 받는다(`WorkRequestVersionRequest`) — 모르는 필드를 실으면 422 다.
     그래서 철회에는 사유 칸을 두지 않는다. 계약이 열리면 그때 싣는다. */
  return request<WorkRequest>(`/api/work-requests/${requestId}/withdraw`, {
    body: JSON.stringify({ expected_version: expectedVersion }),
    method: "POST",
  });
}

/**
 * 취소 항목 목록 정리 (SPEC-003 §4 · L-6 · F-6).
 *
 * **요청자 목록에서만 빠지고 이력은 남는다.** 그래서 화면은 이것을 「삭제」가 아니라 「숨기기」로 부른다.
 */
export async function hideWorkRequestListEntry(requestId: string): Promise<void> {
  await request<void>(`/api/work-requests/${requestId}/list-entry`, { method: "DELETE" });
}

export async function getConversations(limit?: number): Promise<Conversation[]> {
  const query = limit !== undefined ? `?limit=${limit}` : "";
  return request<Conversation[]>(`/api/conversations${query}`);
}

export async function getNotifications(): Promise<Notification[]> {
  return request<Notification[]>("/api/notifications");
}

export async function markNotificationRead(notificationId: string): Promise<Notification> {
  return request<Notification>(`/api/notifications/${notificationId}/read`, { method: "POST", body: "{}" });
}

/** `beforeSequence` pages further back — the oldest message currently held, to load the batch just before it. */
export async function getConversation(conversationId: string, beforeSequence?: number): Promise<Conversation> {
  const query = beforeSequence !== undefined ? `?before_sequence=${beforeSequence}` : "";
  return request<Conversation>(`/api/conversations/${conversationId}${query}`);
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

export async function getProjectParticipationHistory(projectId: string): Promise<ProjectParticipation[]> {
  return request<ProjectParticipation[]>(`/api/projects/${projectId}/participation-history`);
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

export async function releaseFromProject(
  projectId: string,
  memberId: string,
  assignmentId?: string,
  reason?: string,
): Promise<void> {
  const cleaned = reason?.trim();
  const body = {
    ...(assignmentId ? { assignment_id: assignmentId } : {}),
    ...(cleaned ? { reason: cleaned } : {}),
  };
  await request<void>(`/api/projects/${projectId}/members/${memberId}`, {
    method: "DELETE",
    ...(Object.keys(body).length ? { body: JSON.stringify(body) } : {}),
  });
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

/** 관리자 직접 배정 — 조직 범위 검사는 그대로고, W1 부터 수락을 기다리지 않고 즉시 활성 담당으로 선다. */
export async function assignTask(
  title: string,
  assigneeId: string,
  extra: { description?: string; start_date?: string; due_date?: string; checklist?: string[] },
  idempotencyKey: string,
): Promise<TaskAssignment> {
  return request<TaskAssignment>("/api/tasks/assign", {
    body: JSON.stringify({ title, assignee_id: assigneeId, ...extra }),
    headers: { "Idempotency-Key": idempotencyKey },
    method: "POST",
  });
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

/**
 * 승격 모달의 담당 후보 — **참석자 먼저 · 조직도 전체 · 본인 포함** (§9-5 · D40).
 *
 * `getWorkRequestAssigneeCandidates`·`getTaskAssignmentCandidates` 를 쓰지 않는다. 그 둘은
 * 「내가 남에게 배정·요청할 수 있는 범위」라 **누른 사람의 권한**으로 좁히는데, 승격의 요청 주체는
 * 회의(시스템)라 그 권한을 타면 안 된다 — 실측에서 6명 중 2명만 떴고, 배정 권한이 없는 사람은
 * 아예 **403** 을 받았다 (사용자 결정 2026-09-14 §조사 근거 5).
 *
 * 여는 사람이 그 회의의 참석자가 아니면 **404** 다. 응답 모양은 다른 후보 목록과 같다.
 */
export async function getMeetingPromotionCandidates(meetingId: string): Promise<Persona[]> {
  return request<Persona[]>(`/api/meetings/${meetingId}/promotion-candidates`);
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

/* ---- 회의 화면 (SCR-105 · SCR-106) — WP-001 이 소유한 계약 ----
   캘린더 안 드로어 방식이 쓰던 옛 회의 함수(달력 목록 · 노트 · 녹음 · realtime · 요약 채택)는
   WP-002 와 함께 지웠다. 브라우저가 STT provider 를 아는 경로는 이제 없다 (SPEC §5.2). */

export async function listMeetings(cursor?: string | null): Promise<MeetingListPayload> {
  return request<MeetingListPayload>(cursor ? `/api/meetings?cursor=${encodeURIComponent(cursor)}` : "/api/meetings");
}

export async function readMeeting(meetingId: string): Promise<MeetingRecord> {
  return request<MeetingRecord>(`/api/meetings/${meetingId}`);
}

export async function bookMeeting(input: {
  title: string;
  purpose?: string | null;
  starts_at: string;
  ends_at: string;
  /** 사옥 회의실 번호. **`null` 이면 예약 시스템을 부르지 않는다** — 「회의실 선택 안 함」이다. */
  room_id?: number | null;
  attendee_ids: string[];
  external_attendees?: string[];
  agendas?: Array<{ title: string }>;
  carried_from_meeting_id?: string | null;
}, idempotencyKey: string): Promise<MeetingRecord> {
  return request<MeetingRecord>("/api/meetings", {
    body: JSON.stringify(input),
    headers: { "Idempotency-Key": idempotencyKey },
    method: "POST",
  });
}

/** 값을 묻지 않고 지금 시작하는 회의. 돌아오는 것은 이미 「진행 중」인 회의다. */
export async function quickStartMeeting(): Promise<MeetingRecord> {
  return request<MeetingRecord>("/api/meetings/quick-start", { body: JSON.stringify({}), method: "POST" });
}

/** 예약값 고치기 — 「예정」·「완료」에서 참석자 전원이 할 수 있다. 열 수 있는지는 `can_edit_info` 가 말한다. */
export async function updateMeetingInfo(
  meetingId: string,
  patch: {
    title?: string | null;
    starts_at?: string;
    ends_at?: string;
    location?: string | null;
    attendee_ids?: string[];
    external_attendees?: string[];
  },
): Promise<MeetingRecord> {
  return request<MeetingRecord>(`/api/meetings/${meetingId}`, { body: JSON.stringify(patch), method: "PATCH" });
}

/** 두 갈래 삭제 — `meeting` 은 회의를 취소하고, `note` 는 회의록만 지운다. */
export async function removeMeeting(meetingId: string, scope: "meeting" | "note"): Promise<void> {
  await request<void>(`/api/meetings/${meetingId}?scope=${scope}`, { method: "DELETE" });
}

export async function startMeeting(meetingId: string): Promise<MeetingRecord> {
  return request<MeetingRecord>(`/api/meetings/${meetingId}/start`, { body: JSON.stringify({}), method: "POST" });
}

export async function endMeeting(meetingId: string): Promise<MeetingRecord> {
  return request<MeetingRecord>(`/api/meetings/${meetingId}/end`, { body: JSON.stringify({}), method: "POST" });
}

export async function addMeetingAgenda(meetingId: string, title: string): Promise<MeetingAgenda> {
  return request<MeetingAgenda>(`/api/meetings/${meetingId}/agendas`, { body: JSON.stringify({ title }), method: "POST" });
}

/**
 * 안건 한 덩어리를 덮어쓴다. `lines` 는 **최종 벌**(`track: "final"`) 안건의 줄 전부이고, 보낸 것이
 * 그대로 남는다 — 빈 줄은 화면이 보내기 전에 버린다.
 *
 * **줄마다 `line_id` 가 함께 간다** (§8-9). 글자 배열은 **422** 다 — 관용을 두지 않았다.
 * 서버가 이 값으로 계보를 가른다:
 *   · id 가 왔고 본문 그대로 → 계보 유지        · id 가 왔고 본문 달라짐 → 그 줄의 계보만 삭제
 *   · id 없이 옴 → 새 줄                        · 모르는 id → 그 줄만 거절(저장 전체는 산다)
 *
 * 원본 두 벌(사람·AI)의 안건에 `lines` 를 보내면 **409** 이고, `concluded` 를 최종 벌 아닌 안건에
 * 보내도 **409** 다 — 그 둘은 최종 벌의 것이다.
 */
/**
 * 메모 **한 줄** 고치기 (백엔드 `6a9c41a` · 보고서 §4). 쓰는 자리(`POST …/lines`)와 **같은 주소 아래
 * 같은 본문**이라 익힐 모양이 하나다. 돌아오는 것은 그 줄 하나이고 `line_id`·`order`·`at_ms`·`author`
 * 가 **바뀌지 않으므로** 화면은 제자리에서 갈아 끼우면 된다.
 *
 * 실패는 셋이다 (§4 표):
 *   · **422** — 빈 글자(공백뿐)이거나 2000자 초과. **빈 줄로 지우려 하지 마라** — 지우는 것은 `DELETE` 다
 *   · **409** — 게이트가 닫혔거나(정리 중·종료·실패) 최종 벌·AI 벌의 줄이다. 상세를 다시 읽어 맞춘다
 *   · **404** — 그 줄이 없거나 그 안건의 줄이 아니거나 **권한이 없다**.
 *     이 모듈은 권한 밖도 「없는 것처럼」 답한다 (§3.2-1) — **403 을 기다리지 마라**
 */
export async function updateMeetingMemoLine(
  meetingId: string,
  agendaId: string,
  lineId: string,
  text: string,
): Promise<MeetingLine> {
  return request<MeetingLine>(`/api/meetings/${meetingId}/agendas/${agendaId}/lines/${lineId}`, {
    body: JSON.stringify({ text }),
    method: "PATCH",
  });
}

/** 메모 한 줄 지우기 — 204. 확인을 받지 않는다(자기가 적은 임시 재료를 걷는 일이다). 실패 코드는 위와 같다. */
export async function removeMeetingMemoLine(meetingId: string, agendaId: string, lineId: string): Promise<void> {
  await request<void>(`/api/meetings/${meetingId}/agendas/${agendaId}/lines/${lineId}`, { method: "DELETE" });
}

export async function updateMeetingAgenda(
  meetingId: string,
  agendaId: string,
  patch: {
    title?: string;
    concluded?: boolean;
    lines?: Array<{ line_id?: string; text: string }>;
    expected_last_saved_at?: string | null;
  },
): Promise<MeetingAgenda> {
  return request<MeetingAgenda>(`/api/meetings/${meetingId}/agendas/${agendaId}`, {
    body: JSON.stringify(patch),
    method: "PATCH",
  });
}

export async function removeMeetingAgenda(meetingId: string, agendaId: string): Promise<void> {
  await request<void>(`/api/meetings/${meetingId}/agendas/${agendaId}`, { method: "DELETE" });
}

/**
 * 회의 중 메모 한 줄 (SPEC §6). 만든 사람만, 「진행 중」에만 — 서버가 정한다.
 * 돌아온 줄을 화면에 붙인다: **낙관 렌더를 하지 않는다.** 저장되지 않은 말을 저장된 것처럼 두지 않는다.
 *
 * TODO(WP-003): 이 엔드포인트는 WP-003 이 소유한다. 계약대로 먼저 부르고 있다.
 */
export async function addMeetingMemoLine(meetingId: string, agendaId: string, text: string): Promise<MeetingLine> {
  return request<MeetingLine>(`/api/meetings/${meetingId}/agendas/${agendaId}/lines`, {
    body: JSON.stringify({ text }),
    method: "POST",
  });
}

/** 「스크립트」 탭 — 끝난 회의도 진행 중 회의도 같은 자리를 읽는다. 열람은 참석·공유. */
export async function readMeetingTranscript(meetingId: string): Promise<MeetingTranscript> {
  return request<MeetingTranscript>(`/api/meetings/${meetingId}/transcript`);
}

/**
 * 후속업무 후보를 **업무 요청으로** 보낸다 (SPEC §9-5) — 갈래는 하나다.
 * 담당은 누르는 사람이 고른다: 이 함수는 고른 값을 그대로 나른다.
 */
/**
 * 회의 후속 승격 — 같은 생성 계약을 지난다(WORK-001 Phase 5).
 *
 * 멱등에는 **두 층**이 있다. 여기 싣는 키는 **생성 층**이고, 후보 자체의 중복 승격은 서버의 후보 잠금
 * (`followup_candidate(..., lock=True)`)이 따로 막는다 — 키가 달라도 같은 후보는 한 건이다. 그래서 키는
 * 후보 identity 에서 만든 안정 값으로 보낸다.
 */
export async function promoteMeetingTodo(
  meetingId: string,
  todoId: string,
  body: { assignee_id: string; title?: string; description?: string; due_date?: string | null; checklist?: string[] },
  idempotencyKey: string,
): Promise<MeetingTodo> {
  return request<MeetingTodo>(`/api/meetings/${meetingId}/todos/${todoId}/promote`, {
    body: JSON.stringify(body),
    headers: { "Idempotency-Key": idempotencyKey },
    method: "POST",
  });
}

/** 안 만들 후보는 확인 없이 지운다 — 아직 업무가 아니다. 승격된 것은 목록에 남는다. */
export async function removeMeetingTodo(meetingId: string, todoId: string): Promise<void> {
  await request<void>(`/api/meetings/${meetingId}/todos/${todoId}`, { method: "DELETE" });
}

/** [다시 시도] — 「실패」에서 합성만 다시 건다. 받은 발화와 메모는 건드리지 않는다. */
export async function retryMeetingFinalize(meetingId: string): Promise<void> {
  await request<unknown>(`/api/meetings/${meetingId}/finalize`, { body: JSON.stringify({}), method: "POST" });
}

/**
 * 내보내기 주소 — **형식은 HTML 하나다** (SPEC §2.2). 고르는 자리를 두지 않는다.
 * 받는 것은 브라우저가 한다: 서버가 `Content-Disposition` 을 실어 보낸다.
 */
export function meetingExportUrl(meetingId: string): string {
  return `/api/meetings/${meetingId}/export?format=html`;
}

/* ---- 회의 자료 (SCAX-WP-005) ---- */

export async function readMeetingMaterials(meetingId: string): Promise<MeetingMaterial[]> {
  return request<MeetingMaterial[]>(`/api/meetings/${meetingId}/materials`);
}

/**
 * 여러 파일을 한 번에 붙인다 — **되는 것만 붙고 안 되는 것은 사유와 함께 돌아온다** (SPEC §10).
 * 한 건도 못 붙으면 422 `meeting_materials_rejected` 이고 사유가 `detail.failed` 에 실린다.
 * `Content-Type` 을 손으로 정하지 않는다: 경계 문자열은 브라우저가 붙인다.
 */
export async function attachMeetingMaterials(meetingId: string, files: File[]): Promise<MeetingMaterialUpload> {
  const form = new FormData();
  for (const file of files) form.append("files", file, file.name);
  const response = await fetch(`/api/meetings/${meetingId}/materials`, {
    body: form,
    credentials: "same-origin",
    method: "POST",
  });
  if (!response.ok) {
    const error = (await response.json().catch(() => ({}))) as ApiErrorBody;
    const message = typeof error.detail === "string" ? error.detail : response.statusText;
    throw new ApiError(response.status, message, error.detail);
  }
  return response.json() as Promise<MeetingMaterialUpload>;
}

/** 떼는 것은 올린 사람이다 — 회의를 만든 사람도 남의 자료를 못 뗀다 (DEC-014). */
export async function detachMeetingMaterial(meetingId: string, materialId: string): Promise<void> {
  await request<void>(`/api/meetings/${meetingId}/materials/${materialId}`, { method: "DELETE" });
}

/** 드로어 본문이 딛는 주소 — PDF 는 이 주소를 그대로 띄우고 Markdown 은 글자로 읽는다. */
export function meetingMaterialContentUrl(meetingId: string, materialId: string): string {
  return `/api/meetings/${meetingId}/materials/${materialId}/content`;
}

/** Markdown 자료의 본문 글자 — 드로어가 글로 읽어 보여 준다. */
export async function readMeetingMaterialText(meetingId: string, materialId: string): Promise<string> {
  const response = await fetch(meetingMaterialContentUrl(meetingId, materialId), { credentials: "same-origin" });
  if (!response.ok) throw new ApiError(response.status, response.statusText);
  return response.text();
}

/* ---- 회의 공유 (SCAX-WP-005) ---- */

export async function readMeetingShares(meetingId: string): Promise<MeetingViewer[]> {
  return request<MeetingViewer[]>(`/api/meetings/${meetingId}/shares`);
}

/**
 * 여러 명에게 한 번에 연다. **이미 참석이거나 이미 열람인 사람은 서버가 조용히 건너뛴다.**
 * 알림은 가지 않는다 — 목록에 담기는 것이 유일한 도달 경로다 (SPEC §2.2).
 */
export async function shareMeetingWith(meetingId: string, memberIds: string[]): Promise<MeetingViewer[]> {
  return request<MeetingViewer[]>(`/api/meetings/${meetingId}/shares`, {
    body: JSON.stringify({ member_ids: memberIds }),
    method: "POST",
  });
}

/**
 * 공유로 들어온 열람만 거둔다 — 참석을 빼는 자리는 회의 정보 편집이다 (§3.2-6, 참석자면 409).
 * 거둔 뒤의 「볼 수 있는 사람」이 그대로 돌아온다 — 다시 묻지 않는다.
 */
export async function revokeMeetingShare(meetingId: string, memberId: string): Promise<MeetingViewer[]> {
  return request<MeetingViewer[]>(`/api/meetings/${meetingId}/shares/${memberId}`, { method: "DELETE" });
}

/**
 * 고를 수 있는 사옥 회의실. 예약 시스템이 없거나 닿지 않으면 **빈 목록**이 온다 — 없는 방을 지어내지 않는다.
 * 시간대를 주면 **그 시간에 쓸 수 있는 방만** 온다 — 못 잡을 방을 고르게 두지 않는다.
 */
export async function readMeetingRooms(range?: { starts_at: string; ends_at: string }): Promise<MeetingRoom[]> {
  const query = range ? `?starts_at=${encodeURIComponent(range.starts_at)}&ends_at=${encodeURIComponent(range.ends_at)}` : "";
  return request<MeetingRoom[]>(`/api/meetings/rooms${query}`);
}
