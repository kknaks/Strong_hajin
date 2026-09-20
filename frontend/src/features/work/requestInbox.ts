import type { WorkRequest } from "../../lib/viewModels";

export type RequestInboxItem = WorkRequest & { category: "work" | "reference" };

/**
 * 이 항목에 `[읽음]` 을 그릴 수 있나 (SPEC-001 U-12).
 *
 * **참고 갈래이고, 내가 그 요청의 참조자일 때만**이다. 권한이 없으면 비활성이 아니라 **아예 그리지
 * 않는다**(U-11 과 같은 규칙) — 누를 수 없는 단추를 세우면 사람은 자기가 무엇을 잘못했는지 찾는다.
 *
 * 서버가 참조자 목록을 내지 않는 응답에서는 **갈래를 믿는다**: 수신함은 나에게 온 것만 내므로
 * 참고 갈래로 분류됐다는 것이 곧 내가 참조자라는 뜻이다. 없는 값을 「아니다」로 단정하지 않는다.
 */
export function canMarkRead(item: RequestInboxItem, personaId: string): boolean {
  if (item.category !== "reference") return false;
  const cc = item.cc_member_ids;
  return cc ? cc.includes(personaId) : true;
}

/** 서버 category가 우선이다. 미제공 서버에서는 inbox + 권한 내 CC 목록으로만 보완한다. */
export function requestInboxItems(inbox: WorkRequest[], requests: WorkRequest[], personaId: string): RequestInboxItem[] {
  const items = new Map<string, RequestInboxItem>();
  for (const request of requests) {
    if (request.category === "reference" || (!request.category && request.cc_member_ids?.includes(personaId))) {
      items.set(request.request_id, { ...request, category: "reference" });
    }
  }
  for (const request of inbox) {
    const category = request.category ?? (request.assignee_id === personaId ? "work" : request.cc_member_ids?.includes(personaId) ? "reference" : null);
    if (category) items.set(request.request_id, { ...request, category });
  }
  return [...items.values()];
}
