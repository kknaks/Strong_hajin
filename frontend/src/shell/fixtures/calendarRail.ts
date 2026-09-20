import { addDays } from "../../lib/labels";
import type { DirectTask } from "../../lib/viewModels";

/** 화면 검증 전용. 호출자가 기준일을 지정하며 API/BE seed에는 반영하지 않는다. */
export function calendarRailTasks(today: string): DirectTask[] {
  return [
    { task_id: "preview-today", title: "오늘 업무 검토", state: "open", version: 1, block_reason: null, due_date: today },
    { task_id: "preview-long", title: "여러 줄로 표시되는 긴 업무 제목과 캘린더 레일 안쪽 여백 확인을 위한 검토 업무", state: "blocked", version: 1, block_reason: "검토 자료 대기", due_date: today },
    { task_id: "preview-next", title: "다음 날 업무", state: "in_progress", version: 1, block_reason: null, due_date: addDays(today, 1) },
    { task_id: "preview-cancelled", title: "취소한 업무는 일정에 표시하지 않음", state: "cancelled", version: 1, block_reason: null, due_date: addDays(today, 2) },
  ];
}
