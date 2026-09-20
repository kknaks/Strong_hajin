import type { RequestInboxItem } from "../../features/work/requestInbox";

/** UI 전용 fixture. TaskReference가 아닌 WorkRequest CC를 참고로 사용한다. */
export const requestInboxFixture: RequestInboxItem[] = [
  { request_id: "preview-work", title: "분기 보고서 검토 요청", description: "검토 의견을 정리해 주세요.", state: "pending", version: 1, task_id: "preview-task", assignment_state: null, conditions: null, assignee_id: "me", requester_id: "colleague", category: "work" },
  { request_id: "preview-negotiating", title: "여러 줄 제목과 긴 설명의 줄바꿈을 확인하기 위한 업무 요청", description: "협의한 범위와 일정을 확인해 주세요.", state: "negotiating", version: 2, task_id: "preview-task-2", assignment_state: null, conditions: { note: "기한 협의" }, assignee_id: "me", requester_id: "colleague", category: "work" },
  { request_id: "preview-reference", title: "참조로 받은 처리 완료 요청", state: "accepted", version: 2, task_id: "preview-task-3", assignment_state: "active", conditions: null, assignee_id: "colleague", requester_id: "another", cc_member_ids: ["me"], category: "reference" },
];
