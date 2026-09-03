import type {
  DailyReportDraft,
  DailyReportHistory,
  DailyReportStatus,
  DirectTask,
  MyWorkItem,
  OrganizationProfile,
  Persona,
  ActionItem,
  WorkRequest,
  Conversation,
  ConversationContextReference,
  ConversationMessageAcceptance,
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

export async function getMyWork(): Promise<MyWorkItem[]> {
  return request<MyWorkItem[]>("/api/my-work");
}

export async function getTasks(includeClosed = false): Promise<DirectTask[]> {
  return request<DirectTask[]>(includeClosed ? "/api/tasks?include_closed=true" : "/api/tasks");
}

export async function getTask(taskId: string): Promise<DirectTask> {
  return request<DirectTask>(`/api/tasks/${taskId}`);
}

export async function getMyOrganizationProfile(): Promise<OrganizationProfile> {
  return request<OrganizationProfile>("/api/organization/me");
}

export async function createDirectTask(title: string): Promise<void> {
  await request("/api/tasks", {
    body: JSON.stringify({ title }),
    method: "POST",
  });
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

export async function getActionInbox(): Promise<WorkRequest[]> {
  return request<WorkRequest[]>("/api/action-inbox");
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
): Promise<WorkRequest> {
  return request<WorkRequest>("/api/work-requests", {
    body: JSON.stringify({ title, assignee_id: assigneeId }),
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
