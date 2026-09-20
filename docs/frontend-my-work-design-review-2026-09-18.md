# 업무 화면 수정 및 검증

정본: `/Users/kknaks/orca/workspaces/kknaks_profile/strong_hajin/reference/2026-09-10-sc-meeting/package 2/MyWork.html` 및 해당 HTML이 참조하는 shell/my-work CSS·JSX.

## 반영 내용

- 화면 제목 `업무`, 정본의 24px 봉투(inbox) 아이콘.
- 업무 화면에 중복 적용되던 상단 12px·좌우 40px 여백 제거. 탭 좌우 24px, 칩 바 상하 16px·좌우 24px, 칩 사이 8px. 관련 색상 토큰은 정본과 일치하여 유지.
- 주간·월간에서 일정 유무와 무관하게 오늘 dot 표시. 기존 일정 dot·날짜 선택·기간 이동 유지.
- 사이드바: 알림 → 설정 → 홈 → 업무 → 캘린더 → 프로젝트 → 자료함 → 회의 → 조직. 기존 보고·관계 탐색은 뒤에 유지. 프로젝트는 folder 아이콘 사용.
- 자료함은 독립 화면/라우트가 없어 비활성 표시. 새 자료함 연결은 별도 구현이 필요하다. 알림의 기존 비활성 동작과 설정 모달 동작은 유지.
- `GET /api/work-requests/inbox`를 연결. 현재 서버가 요구하는 `work_request.decide` 권한이 있을 때만 호출.
- 수신함 전체/업무/참고: 담당자 inbox와 기존 권한 내 요청 목록의 CC 요청을 request_id로 중복 제거하여 합침. 서버 category가 제공되면 work/reference 값을 우선 사용. 현재 서버의 category 미제공 응답에는 assignee/CC 관계로 대응.
- 업무 요청은 기존 상세를 다시 읽어 수락/거절. 참고 요청은 상태·결과·이력만 읽으며 처리/편집/댓글/업로드 명령을 노출하지 않음.
- 수락 전 요청은 담당 목록에서 제외. 수락 후 기존 task_id의 업무가 나타남. TaskReference와 AX action-items는 수신함 원천으로 사용하지 않음. 기존 홈/채팅/업무 출처 링크의 AX 판단 경로는 유지.
- 프론트 전용 fixture와 Storybook 사례: 데이터 있음, 빈 상태, 로딩, 오류, 긴 제목, 협의 요청, 처리된 CC 요청, 오늘·주간·월간.
- Storybook의 삭제된 styles.css 참조를 제품의 styles/index.css로 수정.

## 검증

- `make frontend-test`: 55 파일, 722 테스트 통과.
- `make frontend-build`: TypeScript (`tsc -b`) 및 Vite 빌드 통과. 500kB 초과 번들 경고 있음.
- `git diff --check`: 통과.
- Headless Chromium: API를 프론트 fixture로 대체한 1920×1080 업무 전체 화면 확인. 탭/칩 계산된 여백 측정. Storybook 수신함/참고/빈 월간 화면 캡처 확인.
- 실제 백엔드 통합 실행은 수행하지 않음. 이번 작업에서 BE 및 seed 파일을 수정하지 않았으며, 작업 중 보인 별도 BE/seed 변경은 건드리지 않음.
- 커밋·푸시 없음.

## 변경 파일

제품:

- `frontend/src/App.tsx`
- `frontend/src/ds/icons/Icon.tsx`
- `frontend/src/features/work/MyWorkPage.tsx`
- `frontend/src/features/work/WorkModals.tsx`
- `frontend/src/features/work/workRows.ts`
- `frontend/src/features/work/requestInbox.ts` (추가)
- `frontend/src/lib/api.ts`
- `frontend/src/lib/viewModels.ts`
- `frontend/src/shell/CalendarRail.tsx`
- `frontend/src/shell/InboxRail.tsx`
- `frontend/src/styles/shell.css`

검증·fixture:

- `frontend/src/App.test.tsx`
- `frontend/src/features/work/MyWorkPage.test.tsx`
- `frontend/src/features/work/workRows.test.ts`
- `frontend/src/features/work/requestInbox.test.ts` (추가)
- `frontend/src/shell/AppShell.test.tsx`
- `frontend/src/shell/CalendarRail.test.tsx` (추가)
- `frontend/src/shell/fixtures/calendarRail.ts` (추가)
- `frontend/src/shell/fixtures/requestInbox.ts` (추가)
- `frontend/.storybook/preview.ts`
- `.design-sync/previews/WorkCalendar.tsx` (추가)
- `.design-sync/previews/WorkInbox.tsx` (추가)
- `docs/frontend-my-work-design-review-2026-09-18.md` (이 보고서)
