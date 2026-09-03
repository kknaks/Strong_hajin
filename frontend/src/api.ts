import type { MyWorkItem, OrganizationProfile, Persona } from "./viewModels";

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
  reason?: string,
): Promise<void> {
  await request(`/api/tasks/${taskId}/${action}`, personaId, {
    body: reason ? JSON.stringify({ reason }) : undefined,
    method: "POST",
  });
}
