/* Screen data for 「내 업무」 — copy is exactly the current screen's copy. */

const INBOX_FILTERS = [
  { value: 'all', label: '전체' },
  { value: 'task', label: '업무' },
  { value: 'cc', label: '참고' },
];

const INBOX_ITEMS = [
  {
    id: 'i1',
    kind: 'task',
    badge: '업무',
    title: '8월 매출 자료 오늘까지 보내주세요.',
    excerpt: '데모에서 업무·회의·자료가 채팅에서 안전하게 이어지는 경험을 검증한다. 여섯줄까지 말줄임 없이 노...',
    who: '홍길동',
    avatar: './assets/avatar-person.png',
    date: '2026.10.10',
    source: '메일',
    sourceIcon: 'mail',
    primary: '내 업무로',
    secondary: '답장',
  },
  {
    id: 'i2',
    kind: 'cc',
    badge: '참고',
    title: '[9월 정산 자료 취합]에 참조로 걸렸습니다',
    who: '홍길동',
    avatar: './assets/avatar-person.png',
    date: '2026.10.10',
    source: '메일',
    sourceIcon: 'mail',
    primary: '원문 확인하기',
    secondary: '확인완료',
  },
  {
    id: 'i3',
    kind: 'cc',
    badge: '참고',
    title: '[사내 공지] 추석 연휴 근무 안내',
    who: '총무팀',
    avatar: './assets/avatar-company.png',
    date: '2026.10.10',
    source: '메신저',
    sourceIcon: 'message',
    primary: '원문 확인하기',
    secondary: '확인완료',
  },
];

const WORK_TABS = [
  { value: 'my', label: '내 업무' },
  { value: 'sent', label: '보낸 업무' },
  { value: 'done', label: '완료 업무' },
];

const TASK_FILTERS = [
  { value: 'all', label: '전체 (00)' },
  { value: 'request', label: '받은 요청 (0)' },
  { value: 'blocked', label: '막힘 (0)' },
  { value: 'overdue', label: '기한 지남(0)' },
];

const STATUS_OPTIONS = [
  { value: 'progress', label: '진행 중', tone: 'accent' },
  { value: 'blocked', label: '막힘', tone: 'danger' },
  { value: 'done', label: '완료', tone: 'positive' },
];

/* 내 업무 표 — 공용 WORK_TASKS 에서 파생 (../../shared/js/work-data.js) */
const TASK_ROWS = (window.WORK_TASKS || []).filter((t) => t.group === 'my').map((t) => ({
  id: t.id, title: t.title, badge: t.badge, badgeTone: t.badgeTone,
  requester: t.requester || '-', due: window.workDueLabel(t),
  status: t.status, starred: t.starred, unread: t.unread, actions: t.actions,
}));


const CALENDAR_RANGES = [
  { value: 'today', label: '오늘' },
  { value: 'week', label: '주' },
  { value: 'month', label: '월' },
];

/* 오늘(9/15) 일정 — 공용 WORK_SCHEDULE(회의·시간 배정) + 오늘 마감인 업무 */
const WORK_TODAY = 15;
const AGENDA_ITEMS = (window.WORK_SCHEDULE || []).filter((x) => x.day === WORK_TODAY).map((x) => ({
  id: x.id,
  badge: x.kind === 'meeting' ? '회의' : '업무',
  tone: x.kind === 'meeting' ? 'neutral' : 'accent',
  time: `${x.start} - ${x.end}`,
  title: x.title,
  sub: x.place,
})).concat((window.WORK_TASKS || [])
  .filter((t) => t.group !== 'done' && (t.due === WORK_TODAY || t.to === WORK_TODAY))
  .map((t) => ({ id: t.id, badge: '업무', tone: 'accent', title: t.title, sub: '오늘 마감' })));


const WORK_DAY = { date: '9월 7일 월요일', hours: '09:00 - 18:00' };

const INBOX_UNREAD_COUNT = 5;


/* ---- 보낸 업무 ---- */
const SENT_TASK_FILTERS = [
  { value: 'all', label: '전체 (00)' },
  { value: 'not-started', label: '시작 안함 (0)' },
  { value: 'overdue', label: '기한 지남 (0)' },
];

const SENT_ROWS = (window.WORK_TASKS || []).filter((t) => t.group === 'sent').map((t) => ({
  id: t.id, title: t.title, assignee: t.owner, due: window.workDueLabel(t), status: t.status,
}));


const SENT_STATUS_LABEL = { progress: '진행 중', 'not-started': '시작 전' };

/* ---- 완료 업무 ---- */
const DONE_TASK_FILTERS = [
  { value: 'all', label: '전체 (00)' },
  { value: 'await', label: '확인 대기 (0)' },
];

const DONE_ROWS = (window.WORK_TASKS || []).filter((t) => t.group === 'done').map((t) => ({
  id: t.id, title: t.title, counterpart: t.counterpart || '-', closedAt: t.closedAt, status: t.status, sent: t.sent,
}));

const DONE_GROUPS = [
  { id: 'g1', label: '내 업무', rows: DONE_ROWS.filter((r) => !r.sent) },
  { id: 'g2', label: '보낸 업무', rows: DONE_ROWS.filter((r) => r.sent) },
];


const DONE_STATUS = { done: { label: '완료', tone: 'info' }, cancelled: { label: '취소', tone: 'danger' } };

/* ---- 주간 캘린더 ---- */
const WEEK_LABEL = '2026년 9월 2주 차';
const WEEKDAY_HEADS = ['월', '화', '수', '목', '금', '토', '일'];
const WEEK_DAYS = [
  { date: 7, dot: false }, { date: 8, dot: false }, { date: 9, dot: false },
  { date: 10, dot: true, selected: true }, { date: 11, dot: false }, { date: 12, dot: false }, { date: 13, dot: false },
];
const WEEK_SECTIONS = [
  { id: 'w1', label: '9월 7일 월요일', count: 1, items: [] },
  { id: 'w2', label: '9월 8일 화요일', count: 2, items: [] },
  { id: 'w3', label: '9월 8일 수요일', count: 0, items: [] },
  { id: 'w4', label: '9월 10일 목요일', count: 3, open: true, items: AGENDA_ITEMS },
  { id: 'w5', label: '9월 11일 금요일', count: 2, items: [] },
];

/* ---- 월간 캘린더 ---- */
const MONTH_LABEL = '2026년 9월';
const MONTH_WEEKS = [
  [{ date: 1, dot: true }, { date: 2, dot: true }, { date: 3, dot: true }, { date: 4 }, { date: 5 }, { date: 6 }, null],
  [{ date: 7 }, { date: 8 }, { date: 9 }, { date: 10, dot: true, selected: true }, { date: 11 }, { date: 12 }, { date: 13 }],
  [{ date: 14 }, { date: 15 }, { date: 16 }, { date: 17 }, { date: 18 }, { date: 19 }, { date: 20 }],
  [{ date: 21 }, { date: 22 }, { date: 23 }, { date: 24, muted: true }, { date: 25, muted: true }, { date: 26, muted: true }, { date: 27 }],
  [{ date: 28 }, { date: 29 }, { date: 30 }, null, null, null, null],
];


/* ---- 업무 만들기 모달 (MOD-101) ---- */
const MODAL_SOURCE = { icon: 'mail', label: '계약서 검토 부탁드립니다' };

const TASK_DRAFT = {
  name: '계약서 검토',
  due: '2026-09-08',
  body: '데모에서 업무·회의·자료가 채팅에서 안전하게 이어지는 경험을 검증한다.\n200자 입력할 수 있습니다. 최대 높이를 지정하고 그 이상 길어지면 스크롤이 적용되게 해주세요.',
  bodyLimit: 200,
};

const SUGGESTED_PEOPLE = [
  { id: 'p1', name: '박민철', role: '법무담당', avatar: './assets/avatar-person.png' },
  { id: 'p2', name: '김길동', role: '계약 검토 이력' },
  { id: 'p3', name: '박혜진', role: '팀장' },
];

const PEOPLE_OPTIONS = ['박민철', '박OO', '박OO', '송OO', '이OO', '이OO', '정OO'];

const SAMPLE_FILES = [
  { id: 'f1', icon: 'document', name: 'Document File.html', size: '1.2 MB' },
  { id: 'f2', icon: 'image', name: 'Image File.jpg', size: '1.2 MB' },
  { id: 'f3', icon: 'link', name: 'https://www.medisolveai.com/' },
];

const AGENT_MESSAGE = '홍길동님  업무 시작 전\n일정을 정리해드릴까요?';

Object.assign(window, {
  INBOX_FILTERS, INBOX_ITEMS, WORK_TABS, TASK_FILTERS,
  STATUS_OPTIONS, TASK_ROWS, INBOX_UNREAD_COUNT, CALENDAR_RANGES, AGENDA_ITEMS, WORK_TODAY, WORK_DAY, AGENT_MESSAGE,
  SENT_TASK_FILTERS, SENT_ROWS, SENT_STATUS_LABEL, DONE_TASK_FILTERS, DONE_GROUPS, DONE_STATUS,
  WEEK_LABEL, WEEKDAY_HEADS, WEEK_DAYS, WEEK_SECTIONS, MONTH_LABEL, MONTH_WEEKS,
  MODAL_SOURCE, TASK_DRAFT, SUGGESTED_PEOPLE, PEOPLE_OPTIONS, SAMPLE_FILES,
});
