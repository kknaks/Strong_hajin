# 업무 화면 2차 디자인 수정

## 반영

- 업무 요청 상세: 기존 `Modal`로 전환. 상세·결과·이력·수락/거절·조정 요청 동작과 읽기 전용 참고 화면을 유지.
- 수신함: 처리 가능한 work 카드에 수락/거절 버튼 직접 표시. 기존 명령·버전·거절 사유 계약 사용. reference/CC 카드에는 처리 버튼 없음. 카드 제목은 키보드로 상세를 여는 버튼이며, 직접 처리 버튼 클릭은 상세 열기로 전파되지 않음.
- 직접 거절 실패 시 사유 입력을 유지하고 같은 명령으로 재시도. 요청 처리 중 버튼 비활성화.
- 재개: 목록·상세의 재개 진입 버튼을 작은 outlined-primary 버튼으로 통일. 상태 전이·재개 사유·권한 조건은 유지.
- 업무 보기: 목록/타임라인만 표시. 업무 화면의 칸반 선택·렌더링 제거.
- 타임라인: 같은 정본 패키지의 Projects 간트 규격을 참고해 이름열 200px, 날짜 최소 34px, 행 최소 40px, 막대 22px로 정렬. 월 경계 헤더와 가로 스크롤 추가. 기존 2주 이동·날짜 구간·범위 밖 잘림·날짜 없는 업무 표시 유지.
- 캘린더 레일: `9월 18일 금요일`, `2026년 9월 3주 차`, `2026년 9월` 형식. 범위 라벨 오늘/주/월. 주간의 과거 일정은 기본 접힘, 오늘은 기본 펼침이며 사용자가 다시 접고 펼칠 수 있음. 오늘 dot 유지.

MyWork 정본에는 타임라인 전용 화면이 없어, 같은 패키지의 `handoff/projects/css/projects.css`와 `projects.v1.jsx`에 있는 간트 기하를 참고했다. 기간 계산과 업무 상태 색상 의미는 기존대로 유지했다.

## 검증 결과

- `make frontend-test`: 57 파일, 728 테스트 통과.
- `cd frontend && npx tsc --noEmit`: 종료 코드 0.
- `make frontend-build`: TypeScript 및 Vite 빌드 통과. 기존 500kB 초과 번들 경고 있음.
- `git diff --check`: 통과.
- Headless Chromium, 1920×1080, 프론트 API fixture 사용: 목록/타임라인/주간 캘린더/요청 상세 화면 캡처 후 확인.
- 재개 버튼: 높이 32px, 색상 rgb(84, 103, 247), 테두리 1px rgba(84, 103, 247, 0.2).
- 타임라인 헤더 날짜와 업무 날짜 열의 x 좌표 최대 오차 0px.
- 요청 상세: x=520, y=180, width=880, height=720으로 중앙 배치. 제목 버튼의 Enter로 열기, Escape로 닫기 확인.
- 실제 백엔드 통합 테스트는 실행하지 않음. 백엔드/API/데이터 계약 파일과 다른 미커밋 변경은 수정하지 않음. 커밋/push 없음.

## 이번 2차 변경 파일

제품:

- `frontend/src/features/work/MyWorkPage.tsx`
- `frontend/src/features/work/WorkModals.tsx`
- `frontend/src/features/work/WorkViews.tsx`
- `frontend/src/shell/InboxRail.tsx`
- `frontend/src/shell/CalendarRail.tsx`
- `frontend/src/styles/screens-a.css`

검증·프리뷰:

- `frontend/src/features/work/MyWorkPage.test.tsx`
- `frontend/src/features/work/TaskTimeline.test.tsx` (추가)
- `frontend/src/shell/InboxRail.test.tsx` (추가)
- `frontend/src/shell/CalendarRail.test.tsx`
- `.design-sync/previews/WorkInbox.tsx`
- `docs/frontend-my-work-design-round2-2026-09-18.md` (이 보고서)
