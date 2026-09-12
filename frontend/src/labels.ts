import type { MeetingStatus, TaskState, WorkRequest } from "./viewModels";

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
  "meeting.share": "회의 공유",
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

/** 이전 desktop-only 소비자의 import 호환성을 위한 문구다. 앱은 더 이상 좁은 화면을 이 안내로 대체하지 않는다. */
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

/* ---- 회의 화면 (SCR-105 회의 목록 · SCR-106 회의 상세) ----
   확정 시안 두 장(`design/회의록.dc.html` · `design/회의실.dc.html`)이 정한 문구를 원문 그대로 모은다.
   화면 파일에 문자열을 흩뿌리지 않는다 — 같은 말이 두 화면에서 갈리지 않게 하는 자리다. */

/** 상태 여섯 (SPEC-004 §5.1). 「완료」이지 「정리됨」이 아니다 (D13). */
export const meetingStatusLabel: Record<MeetingStatus, string> = {
  scheduled: "예정",
  in_progress: "진행 중",
  summarizing: "정리 중",
  done: "종료",
  failed: "실패",
  cancelled: "취소",
};

/** 목록 행의 상태 표기 — DS 에 실재하는 변형만 쓴다. 실패는 점이 아니라 글자색으로 선다. */
export const meetingStatusClass: Record<MeetingStatus, string> = {
  scheduled: "status",
  in_progress: "status in_progress",
  summarizing: "status in_progress",
  done: "status done",
  failed: "danger-text",
  cancelled: "status cancelled",
};

/** 상세 머리의 상태 배지는 셋에만 선다 (SCR-106-E02) — 나머지는 배지를 내지 않는다. */
/**
 * 상세 머리의 상태 배지 (SCR-106-E02). **「정리 중」에는 배지를 두지 않는다** (D34) —
 * 회의록 자리의 로딩과 「정리하는 중」 한 줄이 그 사실을 이미 말한다.
 */
export const meetingBadgeClass: Partial<Record<MeetingStatus, string>> = {
  failed: "badge danger",
  cancelled: "badge neutral",
};

/**
 * 안건이 어디서 왔나 (SCREENDEF-005 `E21` · D38). 기획이 정한 넷에 AI 트랙이 세운 것 하나를 더해 다섯이다.
 * **상태와 무관하게 늘 낸다** — 완료된 회의에서도 그 안건이 어디서 왔는지는 사라지지 않는다.
 */
const agendaSourceLabel: Record<string, string> = {
  manual: "직접 입력",
  set: "세트",
  carried: "지난 회의에서 넘어옴",
  derived: "다른 회의에서 파생",
  ai: "AI 정리",
};

/** 모르는 값이면 빈 글자다 — 없는 출처를 지어내지 않고, 그 자리는 그냥 서지 않는다. */
export function meetingAgendaSourceText(source: string): string {
  return agendaSourceLabel[source] ?? "";
}

export const meetingScreen = {
  /* 목록 (SCR-105) */
  title: "회의 목록",
  start: "회의 시작",
  book: "회의 예약",
  upcoming: "예정",
  past: "지난",
  more: "더 보기",
  remove: "삭제",
  listEmpty: "아직 회의가 없습니다. 회의를 예약하거나 바로 시작하면 여기에 쌓입니다.",
  listError: "불러오지 못했습니다. 다시 시도해 주세요.",
  noTitle: "제목 없는 회의",
  sharedTag: "열람",
  panelEmpty: "왼쪽에서 회의를 고르면 회의록이 열립니다.",
  attendCount: (count: number) => `참석 ${count}명`,
  materials: "자료",
  share: "공유",
  export: "내보내기",
  openNote: "회의록 열기",
  bookNext: "다음 회의 예약",
  /* 삭제 두 갈래 (T08 · T09 · T10) */
  deleteTitle: "회의를 취소할까요?",
  deleteBody: "회의 삭제 시 회의 자료가 삭제되고 회의도 취소됩니다.",
  deleteMeeting: "회의 취소",
  deletedMeeting: "회의를 취소했습니다.",
  deletedNote: "회의록을 삭제했습니다.",

  /* 안건 블록 (양쪽 화면이 같은 블록을 쓴다) */
  /** 번호는 **목록에서의 자리**(1부터)다 — 안건의 `order` 값이 아니다. 바로 시작한 회의는 0 에서 시작한다. */
  agendaHead: (position: number, title: string) => `안건 ${position}. ${title}`,
  concluded: "결론 남",
  notConcluded: "결론 안 남",
  todos: "다음 할 일",
  noTodos: "후속업무 후보가 없습니다.",
  promote: "업무 생성",
  // 이미 업무 요청으로 선 후보 — 상태를 말하기만 한다. 그 업무로 가는 길은 이 화면에 두지 않는다 (D24)
  requested: "요청됨",
  dropTodo: "후보 빼기",
  addLine: "내용 줄 추가",
  linePlaceholder: "내용을 한 줄로 적으세요",
  dropLine: "줄 빼기",
  addAgenda: "안건 추가",
  /** 메모를 적다 말고 안건을 세우는 자리 — 드롭다운 바닥의 한 줄. */
  newAgenda: "새 안건",
  agendaPlaceholder: "안건을 적으세요",
  dropAgenda: "안건 빼기",
  agendaRemoveTitle: "안건과 회의록이 함께 삭제됩니다. 삭제할까요?",
  agendaRemoved: "안건을 삭제했습니다.",

  /* 예약 모달 (MOD-102) */
  bookTitle: "회의 예약",
  subject: "회의명",
  subjectPlaceholder: "회의명을 적으세요",
  recent: "최근",
  when: "일시",
  now: "지금",
  purpose: "목적",
  purposePlaceholder: "이 회의를 왜 하는지 한 줄로",
  agenda: "안건",
  attendees: "참석자",
  place: "장소",
  /** 갈래를 두지 않는다 — 위에서 다 고르고 아래는 단추 하나다. */
  createMeeting: "회의 생성",
  /** 장소를 안 고르는 자리. 목록 맨 위에 서고 기본값이다 — 이걸 고르면 회의실을 잡지 않는다. */
  noRoom: "회의실 선택 안 함",
  /** 회의실을 잡는 동안 — 예약 시스템이 20초까지 붙잡을 수 있다. 다시 걸지 않는다. */
  booking: "예약 중",
  peopleSearchPlaceholder: "조직도 내 이름 검색 / 사외 참석자 추가",
  noMatch: "찾는 사람이 없습니다.",
  /** 명부에 없는 이름 — 사외 참석자로 다는 줄 «위» 에 선다. */
  noEmployee: "직원 정보 없음",
  addGuest: "사외 참석자로 추가",
  guest: "사외",
  suggestTitle: (subject: string) => `지난 「${subject}」`,
  suggestApply: "불러오기",
  suggestClose: "제안 닫기",
  /* 회의실 예약이 안 됐을 때 내는 한 줄 — **회의는 이미 만들어졌고 장소만 비어 있다**.
     넷 다 **임시 문구다**: 기획 정본의 확정 문구 집합에 예약 실패 자리가 없다 (보고 미결). */
  roomFailed: {
    room_unavailable: "그 시간엔 이미 예약된 회의실입니다 — 회의는 만들었고 장소는 비어 있습니다.",
    reservation_auth_failed: "회의실 예약 계정을 쓸 수 없습니다 — 회의는 만들었고 장소는 비어 있습니다.",
    reservation_unavailable: "회의실 예약 시스템에 닿지 못했습니다 — 회의는 만들었고 장소는 비어 있습니다.",
    room_reservation_failed: "회의실을 잡지 못했습니다 — 회의는 만들었고 장소는 비어 있습니다.",
  } as Record<string, string>,

  /** 고른 방이 안 돼 다른 방으로 잡혔을 때 — **임시 문구다** (보고 미결). */
  roomReplaced: (roomName: string) => `${roomName}(으)로 예약됐습니다`,

  /* 회의실을 못 잡아 **회의가 만들어지지 않았을 때** 내는 한 줄. 셋 다 **임시 문구다** (보고 미결).
     모달은 닫히지 않는다 — 쓴 것을 그대로 두고 방만 다시 고르면 된다. */
  roomRejected: {
    room_unavailable: "회의실이 이미 예약되어 있습니다",
    reservation_auth_failed: "회의실 예약 계정을 쓸 수 없습니다",
    reservation_unavailable: "회의실 예약 시스템에 닿지 못했습니다",
  } as Record<string, string>,

  discardTitle: "입력 정보는 저장되지 않습니다.",
  discard: "나가기",
  keep: "취소",

  /* 상세 (SCR-106) */
  info: "회의 정보",
  editInfo: "회의 정보 수정",
  save: "저장",
  cancel: "취소",
  end: "회의 종료",
  retry: "다시 시도",
  convertFailed: "회의 내용은 저장됐지만 글로 옮기지 못했습니다. 다시 시도할 수 있습니다.",
  noteHead: "AI 회의록",
  summarizingAt: (at: string) => `정리하는 중 ${at}`,
  /** 「정리 중」의 회의록 자리 — 배지 대신 이 한 줄과 로딩이 선다 (D34). */
  summarizingNow: "정리하는 중",
  lastSaved: (at: string) => `마지막 저장 ${at}`,
  edit: "수정",
  saved: "저장했습니다.",
  tabMemo: "메모",
  tabAi: "AI 요약",
  tabMaterials: "자료",
  tabScript: "스크립트",
  memoPlaceholder: "메모를 남기세요",
  memoSaveFailed: "저장하지 못했습니다. 다시 시도하고 있습니다.",
  sendMemo: "메모 남기기",
  /** 안건이 하나도 없으면 매달 자리가 없어 드롭다운이 비활성이다 — 라벨은 요소명을 그대로 쓴다. */
  memoTargetEmpty: "안건",
  startedBy: (name: string) => `${name} 이 시작함`,

  /* 스트림 상태 줄 — 아래 다섯은 **임시 문구다** (SPEC §13 `OQ-311`).
     기획 정본의 확정 문구 집합에 스트림 거절·끊김 문구가 없다. 확정 문구가 오면 이 자리만 바뀐다. */
  streamConnecting: "연결하는 중",
  streamDisconnected: "연결이 끊겼습니다.",
  streamTaken: "다른 창에서 진행 중입니다.",
  streamNotFound: "볼 수 없는 회의입니다.",
  streamUnauthorized: "다시 로그인해 주세요.",
  /** 안건 저장 충돌 — 확정 문구가 없다 (`OQ-311` 과 같은 자리). 임시다. */
  promoted: (title: string) => `'${title}' 업무 요청을 보냈습니다.`,
  alreadyRequested: "이미 업무 요청으로 보낸 후보입니다.",
  savedElsewhere: "다른 곳에서 먼저 저장됐습니다. 지금 있는 내용으로 바꿔 두었습니다.",
  /** 제목이 비었을 때 합성이 낸 후보 — 아직 제목이 아니다. */
  titleCandidate: (candidate: string) => `제목 후보 ${candidate}`,
  micDenied: "마이크를 쓸 수 없습니다. 스크립트와 AI 요약은 계속 받습니다.",
  materialsEmpty: "첨부한 자료가 없습니다.",
  attach: "자료 첨부",
  download: "내려받기",
  materialRemoveTitle: "이 회의에서 삭제할까요? 자료에는 그대로 남습니다.",
  materialRemoved: "자료를 삭제했습니다.",
  // 화자 이름 매핑(SCR-106-E57)은 데모 범위 밖이다 (결정 D18) — 스크립트의 화자는 「화자 N」 라벨로만 선다.
  // 이름을 다는 자리도, 딸린 문구(T14·T31·T32·[이름 떼기])도 두지 않는다.
  leaveTitle: "저장하지 않은 내용이 있습니다. 나갈까요?",
  leave: "나가기",
  soloVisible: "나만 볼 수 있습니다",
  memoLabel: "메모",
  // 스크립트 탭의 빈 상태 — 시안에 이 자리의 문구가 없고 원문(발화) 계약도 §3 에 없다.
  // 원문 읽기 계약이 서면 이 문구도 확정 문구로 받아야 한다 (보고 미결).
  scriptEmpty: "아직 원문이 없습니다.",
  titleField: "회의명",
  whenField: "날짜 · 시간",
  placeField: "회의실 이름을 적으세요",
  nameSearchPlaceholder: "조직도 내 이름 검색",

  /* 자료 첨부 (MOD-104) */
  attachTitle: "자료 첨부",
  attachDrop: "여기에 끌어다 놓거나",
  attachPick: "파일 선택",
  attachLimit: "한 건당 20MB · PDF · Markdown",
  attachPartial: "일부 파일을 올리지 못했습니다. 아래에서 확인해 주세요.",
  attachSubmit: "첨부",
  attachTooBig: "20MB가 넘는 파일은 첨부할 수 없습니다.",
  // MOD-104-T02 확정 문구. 데모가 받는 것은 PDF · Markdown 둘뿐이라 이 문장이 실제보다 넓게 말한다 —
  // 확정 문구라 고쳐 쓰지 않았다 (시안 리포트 미결 M15).
  attachBadKind: "첨부할 수 없는 형식입니다. 문서 · 이미지 · 압축 파일을 올려 주세요.",
  attachDropFile: "파일 빼기",
  attached: "자료를 첨부했습니다.",

  /* 공유 (MOD-105) */
  shareTitle: "공유",
  shareLead: "추가한 사람은 이 회의록을 읽을 수 있습니다. 수정은 할 수 없습니다.",
  sharePeople: "공유할 사람",
  shareViewers: "볼 수 있는 사람",
  shareUnitEmpty: "이 부서는 모두 볼 수 있습니다.",
  shareHow: "어떻게",
  shareAttending: "참석",
  shareViewing: "열람",
  shareName: "이름",
  shareUnit: "부서",
  unshareTitle: "삭제한 사람은 이 회의록을 볼 수 없습니다. 삭제할까요?",
  // 시안(회의실.dc.html)은 「공유했습니다. 알림을 보냈습니다.」였으나 이 제품은 알림을 보내지 않는다
  // (D10 · SPEC §3.2-5) — 하지 않는 일을 말하지 않는다.
  shared: "공유했습니다.",
} as const;

const weekdayShort = ["일", "월", "화", "수", "목", "금", "토"] as const;

/** 회의 한 줄의 일시 — `MM-DD (요일) HH:MM` (SCR-105-E04). 서울 시각으로 읽는다. */
export function meetingWhen(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    weekday: "short",
  }).formatToParts(date);
  const at = (type: string) => parts.find((part) => part.type === type)?.value ?? "";
  const iso = `${at("year")}-${at("month")}-${at("day")}`;
  const [y, m, d] = iso.split("-").map(Number);
  const weekday = weekdayShort[new Date(Date.UTC(y, m - 1, d)).getUTCDay()];
  return `${at("month")}-${at("day")} (${weekday}) ${at("hour")}:${at("minute")}`;
}

/** 시각만 — `HH:MM` (서울). 마지막 저장 표시와 스크립트 줄이 쓴다. */
export function meetingClock(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleTimeString("en-GB", { timeZone: "Asia/Seoul", hour: "2-digit", minute: "2-digit" });
}

/** 시작~종료 한 줄 — `09-08 (월) 15:30~16:00`. */
export function meetingRange(startsAt: string, endsAt: string): string {
  return `${meetingWhen(startsAt)}~${meetingClock(endsAt)}`;
}

/**
 * 스크립트의 화자 이름. 전사가 주는 것은 목소리를 가른 **번호뿐**이라(「1」·「2」) 화면이 그 앞에 말을 붙인다 —
 * 확인되지 않은 사람에게 이름을 지어내지 않는다 (X-132). 번호가 아닌 라벨이 오면 그대로 쓴다.
 */
export function meetingSpeakerName(label: string): string {
  const text = label.trim();
  if (!text) return "";
  return /^\d+$/.test(text) ? `화자 ${text}` : text;
}

/**
 * 회의 안의 한 시점을 **회의 경과 시간** 으로 (D50 — 벽시계로 내던 D31 은 철회).
 *
 * 계약이 주는 값이 회의 시작에서 흐른 밀리초 그대로다. 녹취를 듣는 사람이 찾는 것도 「몇 시」가 아니라
 * 「회의 시작에서 몇 분」이라, 여기서는 그 값을 바꾸지 않고 읽기만 한다 — 회의 머리의 날짜·시간 범위는
 * 그대로 벽시계다(`meetingRange`).
 *
 * `mm:ss` 로 내고 한 시간을 넘으면 `h:mm:ss`. 값이 없으면 눈금을 비운다 — 시각을 지어내지 않는다.
 */
export function meetingElapsed(atMs: number | null | undefined): string {
  if (typeof atMs !== "number" || !Number.isFinite(atMs)) return "";
  const total = Math.max(0, Math.floor(atMs / 1000));
  const seconds = String(total % 60).padStart(2, "0");
  if (total < 3600) return `${String(Math.floor(total / 60)).padStart(2, "0")}:${seconds}`;
  return `${Math.floor(total / 3600)}:${String(Math.floor(total / 60) % 60).padStart(2, "0")}:${seconds}`;
}

/** `YYYY-MM-DD` (서울) — 머리 편집의 날짜 칸이 쓴다. */
export function meetingDateInput(value: string): string {
  return isoDateInSeoul(value) ?? value.slice(0, 10);
}

/** 30분 단위 시각 목록 (X-149) — 예약과 머리 편집이 같은 눈금을 쓴다. */
export const meetingTimeOptions: string[] = Array.from({ length: 48 }, (_, index) =>
  `${String(Math.floor(index / 2)).padStart(2, "0")}:${index % 2 ? "30" : "00"}`,
);

/** 서울 로컬 날짜·시각을 서버가 받는 순간 값으로 — 화면이 고른 것이 그대로 그 시각이다. */
export function meetingIsoAt(date: string, time: string): string {
  return `${date}T${time}:00+09:00`;
}
