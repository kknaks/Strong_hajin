import type { TaskState, WorkRequest } from "./viewModels";

export const taskStateLabel: Record<TaskState, string> = {
  open: "시작 전",
  in_progress: "진행 중",
  blocked: "막힘",
  completion_submitted: "완료 확인 대기",
  done: "완료",
  cancelled: "취소",
};

export const taskStateTone: Record<TaskState, string> = {
  open: "neutral",
  in_progress: "accent",
  blocked: "danger",
  completion_submitted: "accent",
  done: "success",
  cancelled: "muted",
};

export const workRequestStateLabel: Record<WorkRequest["state"], string> = {
  pending: "판단 대기",
  negotiating: "협의 중",
  accepted: "수락됨",
  rejected: "거절됨",
  withdrawn: "철회됨",
};

export const workRequestStateTone: Record<WorkRequest["state"], string> = {
  pending: "warning",
  negotiating: "accent",
  accepted: "success",
  rejected: "muted",
  withdrawn: "muted",
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

/**
 * 고르는 자리의 달 표기 — 「2026년 9월」. 읽기 전용 그리드 머리글은 숫자 문법(`formatMonth`)을 쓰지만,
 * 날짜를 고르는 피커 헤더는 v2 `13 — CALENDAR` 미니 캘린더의 문장을 따른다.
 */
export function formatMonthLong(year: number, month: number): string {
  return `${year}년 ${month}월`;
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

/**
 * 빈 상태의 기본 행동 이름 — v2 `10 — STATE` 는 "비어 있음"과 "찾지 못함"을 다르게 다룬다.
 * 필터 때문에 비었으면 초기화를, 불러오지 못했으면 다시 시도를 함께 둔다.
 */
export const emptyActionLabel = {
  filter: "필터 초기화",
  error: "다시 시도",
} as const;

/**
 * 내 업무 툴바의 상태 필터 — v2 05 의 "지금 무엇을 보고 있는가"가 툴바 왼쪽에 온다.
 * 순수한 보기 필터라 권한·command 와 무관하다.
 */
export const taskFilterLabel: Record<string, string> = {
  active: "진행 중·시작 전·막힘",
  all: "전체 상태",
  ...taskStateLabel,
};

export const taskFilterOptions = ["active", "all", "open", "in_progress", "blocked", "done", "cancelled"] as const;

/** 값이 없을 때 칸에 남기는 것 — 공백이 아니라 대시다 (v2 12 TABLE). */
export const emptyValue = "—";

/**
 * 이 제품은 데스크톱 전용이고 1280 미만은 지원하지 않는다 (v2 `15 — RESPONSIVE`).
 * 좁은 폭에서 화면을 접는 대신 이 안내 하나로 대신한다.
 */
export const minWidthNotice = {
  title: "화면이 좁습니다",
  description: "가로 1280 이상에서 사용해 주세요. 창을 넓히면 바로 이어서 볼 수 있습니다.",
} as const;

/**
 * 날짜 피커 — v2 `14 — OVERLAY` 의 Popover 위에 얹는 확장이다. 요일 머리글은 캘린더(`13`)와 같은 글자를
 * 쓰고, 일요일에서 시작한다.
 */
export const weekdayNames = ["일", "월", "화", "수", "목", "금", "토"] as const;

export const datePickerLabel = {
  open: "달력 열기",
  previousMonth: "이전 달",
  nextMonth: "다음 달",
  clear: "지우기",
  today: "오늘",
} as const;

/**
 * 선택 목록 — 네이티브 `<select>` 를 쓰지 않는 대신 Popover(v2 `14`) 위에 목록을 얹는다.
 * 카피는 하나씩만 둔다: 고르는 자리는 화면마다 다르지만 "못 찾았다"·"전부"는 늘 같은 말이어야 한다.
 */
export const selectLabel = {
  placeholder: "선택",
  search: "검색",
  noMatch: "조건에 맞는 항목이 없습니다",
  clear: "지우기",
  selectAll: "전체 선택",
  clearAll: "전체 해제",
} as const;

/**
 * 시각 — 표기는 24시간 고정이다. 오전/오후를 쓰지 않는 것은 취향이 아니라, `toLocaleTimeString` 이
 * 로캘에 따라 12시간으로 갈라져 같은 값이 사람마다 다르게 읽히기 때문이다(DateField 와 같은 이유).
 */
export const timeFieldLabel = {
  open: "시각 고르기",
  placeholder: "시각 선택",
  searchPlaceholder: "14:30",
  dash: "–",
  start: "시작 시각",
  end: "종료 시각",
  rangeInvalid: "종료가 시작보다 빠릅니다",
} as const;

/**
 * 조직 화면 v3 — 한 사람을 여섯 축(계층·소속·직책·직급·직무·권한)으로 읽는 화면의 카피.
 *
 * 축마다 붙는 한 줄은 "이 축은 무엇이 아닌가"를 말한다. 여섯 축 가운데 실제 접근을 바꾸는 축은
 * 권한 하나뿐이라는 것이 이 화면의 요지라, 그 문장들을 화면이 아니라 여기에 모아 둔다.
 *
 * 「이력」·「변경」 가운데 서버에 command 가 없는 것은 이 화면이 그리지 않거나 잠가 둔다 —
 * 있지도 않은 관리 기능을 쓸 수 있는 것처럼 보이게 하지 않는다 (SPEC-005 §5).
 */
export const orgScreen = {
  title: "조직",
  adminBadge: "조직 관리 권한 있음",
  readOnlyBadge: "읽기 전용",
  treeTitle: "조직 tree",
  treeMeta: "읽기 전용",
  searchPlaceholder: "사람 이름 검색",
  searchEmpty: "그 이름을 가진 사람이 없습니다",
  treeError: "조직 tree 를 불러오지 못했습니다",
  memberSuffix: "구성원",
  memberError: "구성원을 불러오지 못했습니다",
  memberEmpty: "재직 중인 구성원이 없습니다",
  memberEmptyDescription: "하위 조직을 포함해도 현재 소속된 사람이 없습니다.",
  pickUnit: "조직을 고르세요",
  pickUnitDescription: "왼쪽 tree 에서 조직을 고르면 그 조직의 구성원을 봅니다.",
  pickMember: "구성원을 선택하세요",
  pickMemberDescription: "가운데 목록에서 사람을 고르면 여섯 축을 봅니다.",
  employed: "재직",
  employmentCaption: "재직과 계정은 다른 축입니다",
  hasAccount: "로그인 계정 있음",
  noAccount: "계정 없음",
  history: "이력",
  /** 계층은 스스로 지나온 길을 갖지 않는다 — 소속이 바뀌면 따라 바뀔 뿐이다. */
  historyNotAnAxis: "계층은 소속 축의 이력이 말합니다",
  historyEmpty: "남아 있는 이력이 없습니다",
  historyDenied: "이 구성원의 이력을 볼 수 있는 범위가 아닙니다",
  historyLoading: "이력을 불러오는 중",
  historyNow: "현재",
  historyNoReason: "사유 없음",
  change: "변경",
  changeLogTitle: "변경 기록",
  changeLogEmpty: "이 조직에서 남은 변경이 없습니다",
  changeLogError: "변경 기록을 불러오지 못했습니다",
  changeLogMore: "더 보기",
  /** 핸드오프의 컬럼 순서 그대로 — 시각 120 · 변경 280 · 축 80 · 사유 · 기록자 120. */
  changeLogColumns: { at: "시각", summary: "변경", axis: "축", reason: "사유", actor: "기록자" },
  revokedTitle: "회수된 권한",
  revokedMore: (rest: number) => `${rest}건 더 보기`,
  revokedLess: "접기",
  /**
   * 축 이름과 그 축이 무엇이 아닌지. 순서가 화면 순서다.
   *
   * `axis` 는 이력을 물을 때 서버에 보내는 이름이다. 계층에는 없다 — 계층은 소속이 지나온 길을 다시 그린
   * 것이라 스스로의 이력을 갖지 않는다.
   */
  axes: {
    hierarchy: { label: "계층", caption: "조직 계층은 권한 범위가 아닙니다" },
    affiliation: { label: "소속", caption: "겸직은 권한을 따라오지 않습니다" },
    appointment: { label: "직책", caption: "직급으로 임명을 막지 않습니다. 사원도 팀장이 될 수 있습니다" },
    grade: { label: "직급", caption: "직급 값은 이 화면에서 바꾸지 않습니다" },
    job: { label: "직무", caption: "직무명은 접근 권한이 아닙니다" },
    access: { label: "권한", caption: "여섯 축 가운데 실제 권한을 바꾸는 축은 이것뿐입니다" },
  },
  accessRestricted: "관리 권한이 있는 사람에게만 보입니다",
  accessError: "권한을 불러오지 못했습니다",
  accessEmpty: "부여된 권한이 없습니다",
  /** 권한 변경 Drawer — 부여·회수 둘 다 사유와 함께 기록된다. */
  accessDrawer: {
    label: "권한 변경",
    granted: "부여된 권한",
    role: "역할",
    rolePlaceholder: "역할 선택",
    scope: "범위",
    scopePlaceholder: "범위 선택",
    reason: "사유",
    reasonHelp: "부여와 회수는 이 사유와 함께 기록됩니다",
    reasonRequired: "사유를 적어야 저장할 수 있습니다",
    standardGrant: "보직 표준 부여",
    directGrant: "직접 부여",
    revoke: "회수",
    grant: "권한 부여",
    cancel: "닫기",
    grantSuccess: "권한을 부여했습니다",
    grantFailure: "권한을 부여하지 못했습니다",
    revokeSuccess: "권한을 회수했습니다",
    revokeFailure: "권한을 회수하지 못했습니다",
    confirmTitle: "이 권한을 회수할까요?",
    confirmLabel: "권한 회수",
    lastAdminNote: "조직을 관리할 사람이 아무도 남지 않는 회수는 거절됩니다.",
  },
} as const;

/** 소속 한 줄 — 「제품기획팀 (주) · 디자인팀 (겸직)」. 주소속과 겸직은 같은 자리에 나란히 선다. */
export function membershipSummary(memberships: ReadonlyArray<{ organization_name: string; kind: string }>): string {
  return memberships.map((item) => `${item.organization_name} (${item.kind === "primary" ? "주" : "겸직"})`).join(" · ");
}

/** 재직 상태 — 서버가 정한 값이 정본이고, 화면은 아는 것만 우리말로 바꾼다. */
export function employmentStateText(state: string): string {
  return state === "active" ? orgScreen.employed : state;
}

/**
 * 한 기간을 한 줄로 — 「2026/03/01 – 2026/09/07」. 아직 끝나지 않았으면 「현재」로 닫는다.
 *
 * 핸드오프는 마침표 문법(`2025.03.02`)을 쓰지만, 이 제품의 읽기 전용 날짜는 어느 화면에서나
 * `formatDate` 의 `YYYY/MM/DD` 다. 한 제품에 날짜 문법을 둘 두지 않는다.
 */
export function formatPeriod(from: string | null | undefined, until: string | null | undefined): string {
  return `${formatDate(from)} – ${until ? formatDate(until) : orgScreen.historyNow}`;
}

/**
 * 변경 기록의 시각 — 오늘 것은 「오늘 09:12」, 올해 것은 「08/27 10:05」, 그 밖은 연도까지.
 *
 * 컬럼이 120px 이라 연도를 늘 적을 자리가 없다. 가까운 일일수록 짧게 적는 대신, 구분자는 제품의 날짜
 * 문법(`/`)을 그대로 쓴다.
 */
export function formatActivityTime(value: string | null | undefined): string {
  if (!value) return emptyValue;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const day = isoDateInSeoul(value);
  const time = date.toLocaleTimeString("en-GB", { timeZone: "Asia/Seoul", hour: "2-digit", minute: "2-digit" });
  if (!day) return String(value);
  const today = seoulToday();
  if (day === today) return `오늘 ${time}`;
  const [year, month, dayOfMonth] = day.split("-");
  if (year === today.slice(0, 4)) return `${month}/${dayOfMonth} ${time}`;
  return `${formatDate(day)} ${time}`;
}
