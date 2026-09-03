import type {
  DailyReportDraft,
  DailyReportHistory,
  DailyReportStatus,
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
  detail?: string;
};

async function request<T>(path: string, personaId: string | null, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(personaId ? { "X-Demo-Persona": personaId } : {}),
      ...init.headers,
    },
  });

  if (!response.ok) {
    const error = (await response.json().catch(() => ({}))) as ApiErrorBody;
    throw new Error(error.detail ?? response.statusText);
  }

  return response.json() as Promise<T>;
}

export async function getDeveloperPersonas(): Promise<Persona[]> {
  return request<Persona[]>("/api/developer/personas", null);
}

export async function getMyWork(personaId: string): Promise<MyWorkItem[]> {
  return request<MyWorkItem[]>("/api/my-work", personaId);
}

export async function getMyOrganizationProfile(personaId: string): Promise<OrganizationProfile> {
  return request<OrganizationProfile>("/api/organization/me", personaId);
}

export async function createDirectTask(personaId: string, title: string): Promise<void> {
  await request("/api/tasks", personaId, {
    body: JSON.stringify({ title }),
    method: "POST",
  });
}

export async function transitionDirectTask(
  personaId: string,
  taskId: string,
  action: "start" | "block" | "resume" | "complete" | "cancel",
  expectedVersion: number,
  reason?: string,
): Promise<void> {
  await request(`/api/tasks/${taskId}/${action}`, personaId, {
    body: JSON.stringify({ expected_version: expectedVersion, ...(reason ? { reason } : {}) }),
    method: "POST",
  });
}

export async function generateDailyReportDraft(
  personaId: string,
  reportDate: string,
): Promise<DailyReportDraft> {
  return request<DailyReportDraft>("/api/daily-reports/generate-draft", personaId, {
    body: JSON.stringify({ report_date: reportDate }),
    method: "POST",
  });
}

export async function editDailyReport(
  personaId: string,
  reportId: string,
  draftId: string,
  expectedVersion: number,
  body: string,
): Promise<DailyReportDraft> {
  return request<DailyReportDraft>(`/api/daily-reports/${reportId}/edit`, personaId, {
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
  personaId: string,
  reportId: string,
  draftId: string,
  expectedVersion: number,
  reason?: string,
): Promise<void> {
  await request(`/api/daily-reports/${reportId}/submit`, personaId, {
    body: JSON.stringify({
      draft_id: draftId,
      expected_version: expectedVersion,
      reason: reason ?? null,
    }),
    method: "POST",
  });
}

export async function getDailyReportHistory(
  personaId: string,
  reportId: string,
): Promise<DailyReportHistory> {
  return request<DailyReportHistory>(`/api/daily-reports/${reportId}/history`, personaId);
}

export async function getDailyReportStatus(
  personaId: string,
  reportDate: string,
): Promise<DailyReportStatus> {
  const query = new URLSearchParams({ report_date: reportDate });
  return request<DailyReportStatus>(`/api/daily-reports/status?${query}`, personaId);
}

export async function getActionInbox(personaId: string): Promise<WorkRequest[]> {
  return request<WorkRequest[]>("/api/action-inbox", personaId);
}

export async function getActions(personaId: string): Promise<ActionItem[]> {
  return request<ActionItem[]>("/api/actions", personaId);
}

export async function getWorkRequestAssigneeCandidates(personaId: string): Promise<Persona[]> {
  return request<Persona[]>("/api/work-request-assignee-candidates", personaId);
}

export async function createWorkRequest(
  personaId: string,
  title: string,
  assigneeId: string,
): Promise<WorkRequest> {
  return request<WorkRequest>("/api/work-requests", personaId, {
    body: JSON.stringify({ title, assignee_id: assigneeId }),
    method: "POST",
  });
}

export async function decideWorkRequest(
  personaId: string,
  requestId: string,
  action: "accept" | "reject",
  expectedVersion: number,
  reason?: string,
): Promise<WorkRequest> {
  return request<WorkRequest>(`/api/work-requests/${requestId}/${action}`, personaId, {
    body: JSON.stringify({ expected_version: expectedVersion, reason: reason ?? null }),
    method: "POST",
  });
}

export async function negotiateWorkRequest(
  personaId: string,
  requestId: string,
  expectedVersion: number,
  conditions: Record<string, string>,
): Promise<WorkRequest> {
  return request<WorkRequest>(`/api/work-requests/${requestId}/negotiate`, personaId, {
    body: JSON.stringify({ expected_version: expectedVersion, conditions }),
    method: "POST",
  });
}

export async function getConversations(personaId: string): Promise<Conversation[]> {
  return request<Conversation[]>("/api/conversations", personaId);
}

export async function getConversation(personaId: string, conversationId: string): Promise<Conversation> {
  return request<Conversation>(`/api/conversations/${conversationId}`, personaId);
}

export async function createConversation(personaId: string, title = "새 대화"): Promise<Conversation> {
  return request<Conversation>("/api/conversations", personaId, { body: JSON.stringify({ title }), method: "POST" });
}

export async function sendConversationMessage(
  personaId: string,
  conversationId: string,
  body: string,
  context: ConversationContextReference[],
  idempotencyKey: string,
): Promise<ConversationMessageAcceptance> {
  return request<ConversationMessageAcceptance>(`/api/conversations/${conversationId}/messages`, personaId, {
    body: JSON.stringify({ body, context }),
    headers: { "Idempotency-Key": idempotencyKey },
    method: "POST",
  });
}

export async function decideAction(
  personaId: string,
  actionId: string,
  expectedVersion: number,
  decision: "approve" | "reject",
): Promise<void> {
  await request(`/api/actions/${actionId}/decide`, personaId, {
    body: JSON.stringify({ expected_version: expectedVersion, decision }),
    method: "POST",
  });
}

export async function cancelConversation(
  personaId: string,
  conversationId: string,
  expectedVersion: number,
): Promise<void> {
  await request(`/api/conversations/${conversationId}/cancel`, personaId, {
    body: JSON.stringify({ expected_version: expectedVersion }),
    method: "POST",
  });
}
