import type { TaskState, WorkRequest } from "./viewModels";

export const taskStateLabel: Record<TaskState, string> = {
  open: "시작 전",
  in_progress: "진행 중",
  blocked: "막힘",
  done: "완료",
  cancelled: "취소",
};

export const taskStateTone: Record<TaskState, string> = {
  open: "neutral",
  in_progress: "accent",
  blocked: "danger",
  done: "success",
  cancelled: "muted",
};

export const workRequestStateLabel: Record<WorkRequest["state"], string> = {
  pending: "판단 대기",
  negotiating: "협의 중",
  accepted: "수락됨",
  rejected: "거절됨",
};

export const workRequestStateTone: Record<WorkRequest["state"], string> = {
  pending: "warning",
  negotiating: "accent",
  accepted: "success",
  rejected: "muted",
};

const executionStateLabel: Record<string, string> = {
  pending: "대기 중",
  queued: "대기 중",
  running: "실행 중",
  completed: "완료",
  failed: "실패",
  denied: "거부됨",
  cancelled: "취소됨",
};

export function executionStateText(state: string): string {
  return executionStateLabel[state] ?? state;
}

const capabilityLabel: Record<string, string> = {
  "action.read": "AX 제안 열람",
  "action.decide": "AX 제안 판단",
  "daily_report.generate": "일일보고 초안 생성",
  "daily_report.read": "일일보고 열람",
  "daily_report.edit": "일일보고 편집",
  "daily_report.submit": "일일보고 제출",
  "meeting.followup.request": "회의 후속 업무 요청",
  "task.read": "업무 열람",
  "task.accept": "배정 수락",
  "task.assign": "업무 배정",
  "task.self_manage": "내 업무 관리",
  "work.read": "업무 원장 열람",
  "work_request.create": "업무 요청 보내기",
  "work_request.read": "업무 요청 열람",
  "work_request.decide": "업무 요청 판단",
};

export function capabilityText(capability: string): string {
  return capabilityLabel[capability] ?? capability;
}

export function personName(displayName: string): string {
  return displayName.replace(/\s*\(.*\)\s*$/, "").trim() || displayName;
}

export function seoulToday(): string {
  return new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Seoul" });
}

/** Read-only calendar dates are shown as YYYY/MM/DD on every surface; inputs and API/DB values stay ISO YYYY-MM-DD. */
export function formatDate(isoDate: string | null | undefined): string {
  if (!isoDate) return "—";
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(isoDate);
  return match ? `${match[1]}/${match[2]}/${match[3]}` : isoDate;
}

/**
 * Observed durations: below one second in milliseconds, from one second on as whole seconds (floored, so a live
 * counter never claims more than was observed). Stored values keep their millisecond precision.
 */
export function formatDuration(ms: number | null | undefined): string | null {
  if (ms === null || ms === undefined || Number.isNaN(ms)) return null;
  const clamped = Math.max(0, ms);
  return clamped < 1000 ? `${Math.round(clamped)}ms` : `${Math.floor(clamped / 1000)}s`;
}

/** Read-only month label (calendar headers): YYYY/MM, the same numeric grammar as formatDate. */
export function formatMonth(year: number, month: number): string {
  return `${String(year).padStart(4, "0")}/${String(month).padStart(2, "0")}`;
}

export function isoDateInSeoul(value: string | undefined | null): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleDateString("en-CA", { timeZone: "Asia/Seoul" });
}

/** Read-only timestamp: YYYY/MM/DD HH:MM in Seoul. Use it wherever the time of day carries meaning. */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const time = date.toLocaleTimeString("en-GB", { timeZone: "Asia/Seoul", hour: "2-digit", minute: "2-digit" });
  return `${formatDate(isoDateInSeoul(value))} ${time}`;
}

/** Hero date: YYYY/MM/DD with the weekday as a secondary cue. */
export function formatLongDate(isoDate: string): string {
  const [year, month, day] = isoDate.split("-").map(Number);
  if (!year || !month || !day) return isoDate;
  const weekday = ["일", "월", "화", "수", "목", "금", "토"][new Date(Date.UTC(year, month - 1, day)).getUTCDay()];
  return `${formatDate(isoDate)} ${weekday}요일`;
}

export function addDays(isoDate: string, days: number): string {
  const [year, month, day] = isoDate.split("-").map(Number);
  const date = new Date(Date.UTC(year, month - 1, day + days));
  return date.toISOString().slice(0, 10);
}

export function dayDifference(fromIso: string, toIso: string): number {
  const [y1, m1, d1] = fromIso.split("-").map(Number);
  const [y2, m2, d2] = toIso.split("-").map(Number);
  return Math.round((Date.UTC(y2, m2 - 1, d2) - Date.UTC(y1, m1 - 1, d1)) / 86_400_000);
}

export function dueDayText(dueDate: string | null | undefined, today: string): string | null {
  if (!dueDate) return null;
  const diff = dayDifference(today, dueDate);
  if (diff === 0) return "D-Day";
  return diff > 0 ? `D-${diff}` : `D+${-diff}`;
}

export function isOverdue(task: { due_date?: string | null; state: string }, today: string): boolean {
  return Boolean(task.due_date) && task.due_date! < today && task.state !== "done" && task.state !== "cancelled";
}
