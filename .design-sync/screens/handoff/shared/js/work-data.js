/* 업무·스케쥴 공용 목데이터 — 업무 화면과 캘린더가 이 한 벌을 같이 쓴다.
   ① WORK_TASKS    업무   : 날짜 단위. group 이 화면의 탭을 가른다 (my 내 업무 · sent 보낸 업무 · done 완료)
   ② WORK_SCHEDULE 스케쥴 : 시간 단위. taskId 가 있으면 그 업무의 「그날 몇 시」 배정, 없으면 회의.
   실제 구현에서는 두 테이블(tasks · schedules) 조회 결과가 이 꼴로 들어온다. */

const WORK_MONTH = { year: 2026, month: 8 };

const WORK_TASKS = [
  /* 내 업무 — 시작~마감 */
  { id: 'w1', group: 'my', title: '제품 소개서 스프린트', from: 2, to: 4, owner: '유하람', requester: '박혜진', status: 'progress' },
  { id: 'w2', group: 'my', title: '채용 온보딩 준비', from: 9, to: 15, owner: '유하람', requester: '박혜진', status: 'progress' },
  { id: 'w3', group: 'my', title: '분기 마감 주간', from: 21, to: 27, owner: '유하람', requester: '-', status: 'progress' },
  { id: 'w4', group: 'my', title: '가격 정책 개편안 작성', from: 16, to: 18, owner: '유하람', requester: '박민수', status: 'blocked', badge: '결정 요청', badgeTone: 'danger', actions: 'accept' },
  /* 내 업무 — 마감기한만 */
  { id: 'w5', group: 'my', title: '경쟁 리서치 정리', due: 3, owner: '유하람', requester: '-', status: 'progress' },
  { id: 'w6', group: 'my', title: '소개서 초안 마감', due: 4, owner: '유하람', requester: '박혜진', status: 'progress', starred: true },
  { id: 'w7', group: 'my', title: '월간 보고 작성', due: 10, owner: '유하람', requester: '-', status: 'progress' },
  { id: 'w8', group: 'my', title: '인터뷰 스크립트 준비', due: 15, owner: '유하람', requester: '박혜진', status: 'progress', badge: '완료 확인', badgeTone: 'neutral', actions: 'confirm' },
  { id: 'w9', group: 'my', title: '9월 정산 자료 취합', due: 16, owner: '유하람', requester: '박민수', status: 'progress', unread: true },
  { id: 'w10', group: 'my', title: '소개서 문구 다듬기', due: 17, owner: '유하람', requester: '-', status: 'progress' },
  { id: 'w11', group: 'my', title: '계약서 조항 검토', due: 18, owner: '유하람', requester: '박민철', status: 'progress' },
  { id: 'w12', group: 'my', title: '주간 보고 마감', due: 25, owner: '유하람', requester: '-', status: 'progress' },
  { id: 'w17', group: 'my', title: '상세 견적 단가 재검토', due: 16, owner: '유하람', requester: '박민철', status: 'progress' },
  { id: 'w18', group: 'my', title: '러닝 데모 계정 정리', due: 16, owner: '유하람', requester: '-', status: 'progress' },
  /* 내 업무 — 기한 없음 */
  { id: 'w14', group: 'my', title: '경쟁사 가격표 모아두기', owner: '유하람', requester: '-', status: 'progress' },
  { id: 'w15', group: 'my', title: '온보딩 문서 목차 정리', owner: '유하람', requester: '-', status: 'progress' },
  { id: 'w16', group: 'my', title: '데모 시나리오 아이디어', owner: '유하람', requester: '-', status: 'progress' },
  /* 보낸 업무 — 내가 남에게 요청한 것 */
  { id: 'x1', group: 'sent', title: '회사 소개 자료 색상 수정', due: 16, owner: '박혜진', status: 'progress' },
  { id: 'x2', group: 'sent', title: '사용자 인터뷰 질문 보완', due: 16, owner: '박혜진', status: 'not-started' },
  { id: 'x3', group: 'sent', title: '이번 주 진행 상황 정리', due: 16, owner: '박민수', status: 'not-started' },
  { id: 'x4', group: 'sent', title: '계약 조항 비교표 작성', due: 24, owner: '박민철', status: 'progress' },
  /* 완료 업무 */
  { id: 'd1', group: 'done', from: 1, to: 2, title: '8월 회고 정리', owner: '유하람', counterpart: '-', closedAt: '09-02', status: 'done' },
  { id: 'd2', group: 'done', due: 4, title: '데모 환경 점검', owner: '유하람', counterpart: '박민수', closedAt: '09-04', status: 'done' },
  { id: 'd3', group: 'done', due: 8, title: '소개서 초판 배포', owner: '유하람', counterpart: '박혜진', closedAt: '09-08', status: 'done' },
  { id: 'd4', group: 'done', due: 9, title: '가격표 초안 회신', owner: '김길동', counterpart: '김길동', closedAt: '09-09', status: 'cancelled', sent: true },
];

const WORK_SCHEDULE = [
  { id: 's1', kind: 'meeting', title: '주간 스크럼', day: 7, start: '11:00', end: '12:00', place: '회의실 B', owner: '유하람', repeat: '매주 월' },
  { id: 's2', kind: 'meeting', title: '채용 인터뷰', day: 10, start: '15:00', end: '16:00', place: '회의실 C', owner: '박혜진' },
  { id: 's3', kind: 'meeting', title: '주간 스크럼', day: 14, start: '11:00', end: '12:00', place: '회의실 B', owner: '유하람', repeat: '매주 월' },
  { id: 's4', kind: 'meeting', title: '제품 소개서 리뷰', day: 15, start: '09:30', end: '10:30', place: '회의실 A', owner: '유하람' },
  { id: 's5', kind: 'meeting', title: '팀 워크숍', day: 16, start: '14:00', end: '17:00', place: '대회의실', owner: '박혜진' },
  { id: 's6', kind: 'meeting', title: '주간 스크럼', day: 21, start: '11:00', end: '12:00', place: '회의실 B', owner: '유하람', repeat: '매주 월' },
  { id: 's7', kind: 'meeting', title: '로드맵 리뷰', day: 23, start: '16:00', end: '17:00', place: '회의실 A', owner: '박혜진' },
  { id: 's8', kind: 'meeting', title: '주간 스크럼', day: 28, start: '11:00', end: '12:00', place: '회의실 B', owner: '유하람', repeat: '매주 월' },
  /* 업무의 시간 배정 */
  { id: 's9', kind: 'task', taskId: 'w2', title: '채용 온보딩 준비', day: 13, start: '13:00', end: '14:00', owner: '유하람' },
];

/* 표에 쓰는 날짜 문구 — 09-16 꼴 */
const workDueLabel = (t) => {
  const d = t.to || t.due;
  return d ? `${String(WORK_MONTH.month + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}` : '-';
};

Object.assign(window, { WORK_MONTH, WORK_TASKS, WORK_SCHEDULE, workDueLabel });
