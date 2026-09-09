# design-sync 노트 — SCAX 디자인 시스템 v2

claude.ai/design 프로젝트 `SCAX` (`8fa54d76-58d6-481a-aed9-743dff9d6c09`) 동기화의 리포 특이사항. 다음 동기화가 같은 삽질을 반복하지 않도록 한 줄씩 남긴다.

## 리포 형태

- 컴포넌트 라이브러리가 아니라 Vite 앱(`frontend/`)이다. 라이브러리 `dist/` 가 없어서 `cfg.entry = ./frontend/ds-entry.tsx` 를 따로 두고 디자인 부품만 내보낸다. 소스 합성 모드(synth-entry)로 두면 `src/*.tsx` 전부(페이지·api·sigma 그래프)가 번들에 들어가므로 쓰지 않는다.
- 부품이 늘면 `frontend/ds-entry.tsx` 와 `cfg.componentSrcMap` 에 같이 추가한다. `componentSrcMap` 이 곧 컴포넌트 목록이다(.d.ts 가 없어서 자동 발견이 안 된다).
- 토큰·컴포넌트 CSS 의 소유자는 `frontend/src/styles.css` 하나(`cfg.cssEntry`). 컴포넌트는 전부 그 CSS 클래스 위의 얇은 래퍼라 provider 가 없다.
- 시각 규칙 SoT 는 `docs/design/design-system-v2.dc.html`(Claude Design 캔버스). 번들에는 넣지 않는다.

## 폰트

- 앱은 Pretendard 를 시스템 설치 폰트에 기댄다(`styles.css`·`index.html` 어디에도 @font-face 없음). 렌더 환경에 파일이 같이 가야 해서 `.design-sync/fonts/` 에 Pretendard Variable(OFL)을 싣고 `cfg.extraFonts` 로 연결했다.

## 렌더 검사 환경

- playwright chromium 캐시는 macOS 에서 `~/Library/Caches/ms-playwright/` 다(`~/.cache/ms-playwright/` 아님). 리포 고정 playwright 1.62.1 = chromium build 1234, 이미 캐시돼 있어 설치 불필요.

## 프리뷰

- 프리뷰는 `.design-sync/previews/<Name>.tsx` 19개 전부 손으로 썼다(리포에 스토리·예제가 없어 컴포넌트 소스 + styles.css 클래스에서 조합). 카피는 SCAX 도메인(업무·회의록·일일보고)의 실제 문구 톤.
- Popover 는 열린 상태를 props 로 강제할 수 없어 프리뷰 안의 `Opened` 래퍼가 마운트 직후 트리거를 click() 한다.
- Skeleton 은 CSS 가 0.4초 뒤에 막대를 보이므로(짧은 로딩엔 안 띄우는 규칙) 정적 캡처가 빈 화면이 된다. 프리뷰 안에서만 `<style>.skeleton-bar{animation-delay:0s}</style>` 로 지연을 끈다 — 앱 CSS 는 손대지 않았다.
- Popover 는 column 카드에서 열린 패널이 셀 밖으로 나가 제품 카드에서 잘렸다(2026-09-07 사용자 보고). `cardMode: single` + `primaryStory: StatusFilter` 로 열린 상태 하나만 카드에 보인다. Closed 스토리는 `?story=Closed` 로만.
- TaskCalendar(`src/WorkViews.tsx`)를 2차 동기화에서 추가했다. 페이지가 아니라 props(tasks·onOpen·mode·anchorDate)만 받는 컴포넌트라 그대로 내보낸다. WorkViews 가 WorkModals 를 끌고 와 번들이 25→33KB.
- 3차 동기화(2026-09-07 밤): frontend 4차 워커가 만든 DatePicker(Popover 위 확장)·Toast tone 추가 → 15개. DatePicker 프리뷰도 Popover 처럼 `Opened` 래퍼로 마운트 직후 연다. 둘 다 **v2 에 없는 확장** — v2 01·14 등록 요청 목록에 올려 둔다.
- 4차 동기화(2026-09-08): `Select`·`MultiSelect`(src/Select.tsx)·`TimeField`·`TimeRangeField`(src/TimeField.tsx) 추가 → **19개**. 두 참조 화면(`templates/select`·`templates/time-field`)을 규격서로 읽어 구현했고, 실물이 대체했으므로 그 두 템플릿은 이 동기화에서 지웠다(`templates/org-chart`·`templates/typography` 는 유지).
- `TimeField` 는 자기 목록을 갖지 않고 **`Select` 의 `OptionPanel` 을 그대로 쓴다**(`Select.tsx` 가 `SelectOptionPanel`·`useTriggerWidth` 를 내보낸다). `Select` 의 패널 props 를 고치면 시각 필드 4개 스토리가 같이 흔들린다 — 둘을 한 묶음으로 본다.
- `MultiSelect` 의 옵션 체크는 `FormControls.tsx` 에서 뺀 `CheckboxBox` 다(`Checkbox` 와 같은 그림 한 벌). `role="option"` 인 단추 안이라 `onChange` 없이 읽기 전용·탭 밖·aria-hidden 으로 그려진다 — 번들 엔트리에는 올리지 않는 내부 부품.
- 프리뷰 카드 폭 함정: `TimeRangeField` 의 WithDate 스토리를 560px 로 두면 카드 오른쪽에서 종료 필드가 **잘린다**(첫 캡처에서 확인). 스토리 500 + `overrides.TimeRangeField.viewport = 620x520` 으로 맞췄다. 한 줄에 필드 셋을 세우는 스토리는 카드 폭을 먼저 재고 쓴다.
- 툴바 트리거 스토리(`Select` ChipTrigger)에는 `filter-chip` 에 `Icon name="chevron-down" size={12}` 를 반드시 같이 둔다 — 가이드의 툴바 골격이 그 모양이라, 빠지면 카드가 규약과 어긋난 예시를 가르친다.
- MinWidthNotice 는 1280 미만 미디어쿼리에서만 보여서 카드 뷰포트를 960x480 으로 둔다(`cfg.overrides`). Drawer/ConfirmModal/Toast 는 position:fixed 라 `cardMode: single` + 뷰포트.
- 텍스트가 긴 스토리(Icon AllGlyphs, DateField, Empty 등 7개)는 `[GRID_OVERFLOW]` 가 떠서 `cardMode: column` 으로 돌렸다.
- `.d.ts` 는 라이브러리 빌드가 없어 자동 추출이 안 된다(`[key: string]: unknown`). 13개 전부 `cfg.dtsPropsFor` 로 손으로 썼다 — 컴포넌트 props 가 바뀌면 여기도 같이 고쳐야 한다.

## Known render warns

- `[GRID_OVERFLOW] Toast (fixed/portal)` — column 카드에서 각 셀이 transform 을 가져 fixed 토스트가 셀 안에 갇힌다. 카드 스크린샷으로 4셀 전부 정상 확인(2026-09-07). single 로 바꾸면 Success/Error 가 안 보여 사용자가 반려했으므로 column 유지.
- Popover·DatePicker 프리뷰의 `Opened` 래퍼는 400ms 마다 닫힘을 감지해 다시 연다 — 제품 카드가 살아 있는 iframe 이라 사용자가 항목을 누르면 닫혀 버렸던 것(2026-09-07 반려)의 대응.

- package-capture 의 리뷰 시트는 시계를 2024-05-15 로 고정해 찍는다. 「오늘」에 기대는 스토리(DatePicker NoValue, TaskCalendar today 셀)는 리뷰 시트에서 날짜가 이상하게 보여도 정상 — validate 의 카드 스크린샷(`_screenshots/general__<Name>.png`)은 실제 시계라 거기서 확인한다.
- DatePicker 카드는 뷰포트 높이가 낮으면 `.popover` 의 `max-height: min(60vh, 420px)` 때문에 푸터가 잘린다 → `cardMode: single` + viewport 520x640.

- `[GRID_OVERFLOW]` 7건은 column 카드로 해소. 이후 재검증에서 새 경고 0.
- 4차 동기화 재검증(19개)에서도 새 경고는 없다 — 남은 warn 은 위의 Toast `escape` 하나뿐이고, 새 부품 4개는 bad/thin/blank/floor 전부 0.

## Re-sync risks

- **dtsPropsFor 가 수동 복사본이다.** `frontend/src/*.tsx` 의 props 시그니처가 바뀌어도 아무것도 경고하지 않는다. 재동기화 때 **19개** 컴포넌트의 props 를 소스와 대조한다. 특히 `Select`·`MultiSelect`·`TimeField`·`TimeRangeField` 는 props 가 많고(트리거 render prop·footerAction·option 배열) 손으로 옮긴 지 얼마 안 됐다.
- **ds-entry.tsx 와 componentSrcMap 은 손으로 맞춘 목록이다.** 새 프리미티브가 src 에 생겨도 자동으로 안 올라간다.
- **폰트는 리포 밖에서 받아 온 파일이다.** `.design-sync/fonts/PretendardVariable.woff2` (jsdelivr npm pretendard@1.3.9). 앱 자체는 이 파일을 안 쓴다.
- **프리뷰 캡처 환경 가정**: node 20 · playwright 1.62.1 (chromium 1234, macOS 캐시 `~/Library/Caches/ms-playwright`). `.ds-sync/node_modules/playwright` 는 `frontend/node_modules/playwright` 로의 심볼릭 링크다 — 새 클론에서는 다시 만든다.
- **네이티브 `<select>` 는 이제 앱에 없다.** 4차 때 7곳(`WorkModals` 5 · `ProjectPage` 1 · `org/AccessDrawer` 2)을 `Select` 로 갈았고, 규약에도 금지로 박았다. 이때 `<label htmlFor>` 는 전부 `<span>` 이 됐다 — 트리거가 단추라 label 이 이름을 주지 못하고, 이름은 `label` prop 이 `aria-label` 로 준다.
- 그 교체는 **테스트를 같이 깬다**: `fireEvent.change(getByLabelText(...), {target:{value}})` 로 네이티브 select 를 몰던 6개(Checklist·CreateWork×2·TaskReferences·ProjectPage·OrgPage)를 「트리거 click → `getByRole("option", {name})` click」 으로 고쳤다. 남은 select 를 더 바꾸면 같은 수정이 따라온다.
- 브랜치 `kknaksss/sc-design-system` 위에서 동기화했고 durable 파일(`.design-sync/`·`frontend/ds-entry.tsx`·`.gitignore`)은 **커밋하지 않은 채** 남겼다 — 커밋은 코디네이터/사용자 판단.
