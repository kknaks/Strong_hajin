import type {
  DailyReportDraft,
  DailyReportHistory,
  MyWorkItem,
  OrganizationProfile,
  Persona,
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
