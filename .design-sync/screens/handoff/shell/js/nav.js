/* 좌측 내비 정본 — 모든 화면이 같은 목록을 쓴다.
   href 가 있는 항목만 이동한다. 아직 화면이 없는 항목은 href 없이 둔다 (비활성). */

const NAV_PRIMARY = [
  { id: 'alert', label: '알림', icon: 'bell', dot: true },
  { id: 'setting', label: '설정', icon: 'setting' },
];

const NAV_MAIN = [
  { id: 'home', label: '홈', icon: 'home' },
  { id: 'work', label: '업무', icon: 'square-check', href: './MyWork.html' },
  { id: 'calendar', label: '캘린더', icon: 'calendar', href: './Calendar.html' },
  { id: 'meeting', label: '회의', icon: 'persons', href: './MeetingWorkspace.html' },
  { id: 'chat', label: '채팅', icon: 'chat' },
  { id: 'inbox', label: '수신함', icon: 'inbox', dot: true },
  { id: 'progress', label: '진행 현황', icon: 'arrow-right' },
  { id: 'files', label: '자료', icon: 'document' },
];

Object.assign(window, { NAV_PRIMARY, NAV_MAIN });
