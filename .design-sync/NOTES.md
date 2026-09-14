# design-sync 노트 — `TheSC AX Design System`

claude.ai/design 프로젝트 **`TheSC AX Design System`** (`7e839512-977c-4142-b4b5-d992df566ffc`) 동기화의
리포 특이사항. 다음 동기화가 같은 삽질을 반복하지 않도록 한 줄씩 남긴다.

---

## ⚠ 2026-09-14 — 구조가 통째로 바뀌었다. 이 절부터 읽어라

앱 전체가 구 디자인 시스템에서 **TheSC AX** 로 옮겨 갔고 구 DS 는 은퇴했다 (PR #12 → `main` `75360fe`).
이 동기화 준비(바퀴 12)는 **`.design-sync/` 와 `frontend/ds-entry.tsx` 만** 손댔다 — 앱 코드는 읽기만 했다.

### ⑨ `7e839512` 에는 원본 Figma DS 가 있었다 — **사용자 판단으로 교체했다** (2026-09-14)

`/design-sync` 를 돌려 `list_files` 를 찍어 보고서야 알았다 — **목표 프로젝트가 빈 통이 아니라,
우리가 부품·토큰을 복사해 온 업스트림 디자인 시스템 그 자체다.**

- `components/` 17그룹 · **199종** · `guidelines/` 19 · `tokens/` 7벌 · `handoff/`(우리 부품 주석이
  「핸드오프」라 부르는 `scax-ui.jsx`·`work-modal.jsx`) · `templates/` · `ui_kits/` · `uploads/` 스크린샷 40여장
- 루트에 이미 `_ds_bundle.js` · `_ds_manifest.json` · `styles.css` 가 있다

스킬의 업로드 계획은 deletes 글롭(`components/**`·`tokens/**`·`guidelines/**`·`fonts/**`·`_vendor/**`·
`_preview/**`)과 **필수** reconciliation(「최종 번들에 없는 원격 경로를 전부 삭제」)을 돈다.
그대로 올렸으면 **업스트림 199종 + guidelines 19 + tokens 7벌이 지워지고** 우리 34종으로 대체됐다.
되돌릴 수 없다. **`finalize_plan` 전에 멈췄고 아무것도 쓰지 않았다.**

**두 프로젝트는 성격이 다르다.** 업스트림 부품은 Figma 노드에서 뽑은 **정적 렌더러**다 —
`className`/`style` + `text1..N`(문자열 슬롯) + `icon1..N`(노드 슬롯) + 부분을 켜고 끄는 boolean 뿐이고,
**열어 본 것 중 `onClick`·`onChange`·`value`·`checked` 를 가진 것이 하나도 없다**
(`Avatar`·`Button`·`ButtonGroup`·`IconButton`·`Icon`·`EmptyPage`·`TaskTable`·`ChipChip`·
`SegmentedControlSegmentedControl` 확인). 유일한 예외가 `components/datetime/` 셋인데, 그 `.d.ts` 가
스스로 「**Intentional addition** — the source Figma file defines no date-picker」라고 적고 있다.
우리 `ds/` 는 반대로 전부 동작하는 앱 부품이다.

**우리 사본의 혈통은 확인된다** (파일을 열어 대조했다):
- `styles/fonts.css` = `tokens/fonts.css` 의 포크. 앞 4줄 주석이 **바이트 단위로 같고** src 만 로컬로 바꿨다.
  (덤: 업스트림의 비-JP URL `@v1.3.9/dist/web/...` 는 jsDelivr 404 다 — 우리 사본이 그걸 고쳐 뒀다)
- `styles/scax.css` = `tokens/scax.css` 의 **순수 상위집합**. 103종 → 142종, **더한 39 · 뺀 0**
  (graph 15 · ai 7 · progress 3 · hero 3 · shadow 4 · warning 2 · surface-selected · popover-current ·
  neutral-soft · radius-chip · radius-panel)
- `ds/icons/glyphs.tsx` 의 24그리드 글리프는 `components/icon/Icon.jsx` 에서 옮겨 온 것이라고 소스 주석이 적는다

**사용자 결정 (2026-09-14)**: 「지금은 정본이 우리 꺼야, 코드 기준」 —
Figma 정적 렌더러 199종 + `tokens/` 7벌을 **지우고** 우리 35종으로 교체했다. 그대로 실행했다.

**지우지 않은 것** (우리 번들에 대체물이 없어서 — 다음 동기화도 지우지 마라):
`guidelines/` 19장(브랜드·색·타이포 카드) · `handoff/` · `templates/` · `ui_kits/` ·
`uploads/`(시안 스크린샷 40여장) · `design_handoff_my_work/` · `assets/`.
`cfg.guidelinesGlob` 을 **`[]` 로 두는 이유가 이것이다** — 기본값은 `docs/*.md` 를 긁어 가는데
이 리포의 `docs/` 는 백엔드·도메인 문서라 `guidelines/` 를 오염시킨다.

### ⑩ 파이프라인은 뚫렸다 — 그러나 **소스가 움직이는 중이면 올리지 마라** (2026-09-14)

`/design-sync` 를 끝까지 돌려 build ✓ · validate **exit 0** · 34/34 렌더 · capture ✓ 까지 갔다.
가는 길에 고친 것 셋 — 다음 동기화는 여기서 시작하면 된다:

1. **`cssEntry` 가 `@import` 묶음이면 아무것도 안 풀린다.** `cfg.cssEntry` 의 내용은
   `_ds_bundle.css` 에 **그대로 이어 붙는다**(`package-build.mjs` 의 `appendFileSync`).
   `src/styles/index.css` 는 값 없이 `@import "./x.css"` 14줄뿐이라 번들 뿌리에서 전부 깨졌다
   (`[CSS_IMPORT_MISSING]` ×14). `copyTokens` 는 `tokensPkg`(= node_modules 패키지)가 있어야만
   도므로 우리처럼 앱 안에 CSS 가 있는 리포에는 안 맞는다.
   → **`.design-sync/flatten-css.mjs`** 로 빌드 직전에 한 벌로 펼치고
   `cfg.cssEntry = ".ds-styles-flat.css"` 를 가리킨다(산출물 `frontend/.ds-styles-flat.css`, gitignore).
   **빌드 전에 반드시 먼저 돌려라** — 안 돌리면 낡은 CSS 가 올라간다.
2. **`Opened` 래퍼는 `aria-expanded` 로 물어야 한다.** 패널은 포털로 `document.body` 에 서므로
   `root.querySelector(".scax-popover")` 는 **영원히 null** 이다 → 400ms 마다 트리거를 다시 눌러
   열림/닫힘을 반복하고 캡처 순간엔 닫혀 있다. `DatePicker`·`Select`·`MultiSelect`·`TimeField`
   넷이 그 상태였다(`Popover` 만 처음부터 `aria-expanded` 를 써서 무사했다).
3. **`position:fixed` 부품은 높이를 주는 래퍼가 없으면 찌부러진다.** `Modal` 프리뷰가 래퍼 없이
   서 있어서 캡처 iframe 의 `100vh` 가 0에 가까워지고 `.scax-modal` 의
   `height:min(720px, calc(100vh - 48px))` 가 따라 접혀 **머리·본문이 사라지고 푸터만 남았다**
   (`.render-check.json` 의 `maxHeight: 48`). `Drawer`·`ConfirmModal` 은 이미 minHeight 래퍼가 있었다.

**업로드 직전에 한 번 멈췄다** — 같은 워크트리에서 **다른 에이전트가 앱 소스를 고치는 중**이었다.
내 빌드(11:47:35) **1분 전**인 11:46:29 에도 `components.css`·`lib/labels.ts` 가 쓰이고 있었고,
`ds/` 표면 13개 파일이 내 기준과 달라져 있었으며 **`ds/Spinner.tsx` 라는 35번째 부품**이 새로 생겼다.
결정적으로 `DropZone` 에 **필수 prop `drop` 이 새로 생겨** 내 `dtsPropsFor` 와 프리뷰가 이미 틀렸다.

편집이 15분간 멎은 것을 확인한 뒤 그 델타를 흡수하고 진행했다 — `Spinner`(35번째) 추가,
`DropZone.drop` 필수 prop, `FileList.icon`, `Toast.icon`, 글리프 3종(collapse·expand·trash).

> **규칙: 올리기 전에 `git status --porcelain frontend/src/` 를 먼저 본다.**
> `_ds_sync.json` 은 내용 해시로 「이 상태를 검증했다」고 보증하는 앵커다. 움직이는 트리에서 올리면
> **반쪽 상태를 보증하게 되고**, 다음 동기화의 diff 는 그것을 영원히 고칠 수 없다.
> 이번에는 «편집이 멎었고 빌드가 그 뒤였다» 를 확인하고 올렸다. **트리는 아직 커밋 안 된 상태다** —
> 그 커밋이 앵커와 어긋나면 다음 동기화가 전량 재검증한다(안전한 쪽이다).

### ⑪ 앱 버그 둘 — 프리뷰로는 못 고친다 (2026-09-14 발견)

1. **`body` 에 `font-family` 가 없다.** 바퀴 2 가 원본 DS 전역 리셋 7줄을 잘라냈고 바퀴 9-B 가
   여덟 줄만 되살렸는데 그중에 `font-family` 가 **없다**. 그런데 `.scax-button`·`.scax-badge`·
   `.scax-chip`·`.scax-table`·`.scax-modal__title`·`.scax-drawer__title`·`.scax-file-row__name` 등
   글자를 내는 클래스 대부분이 스스로 걸지 않고 **body 상속에 기댄다**.
   실측: `getComputedStyle(document.body).fontFamily === "Times"`. 앱 전체 규칙 중
   `html`/`body`/`:root` 에 `font-family` 를 거는 것은 **0개**다(주석 처리된 것 하나뿐).
   → 번들에서는 `flatten-css.mjs` 가 `body{font-family:var(--font-ui);…}` 한 줄을 덧대 막았다.
   **앱 쪽 수정은 `styles/shell.css` 의 전역 element 구획이 맡아야 한다.**
2. **글리프 둘이 비었거나 반쪽이다.** `tune` 은 `glyphs.tsx:232` 에서 `<></>` — **도형이 0개**다
   (`Empty variant="filter"` 가 이걸 쓰므로 「조건에 맞는 항목 없음」 상태의 아이콘이 늘 빈 원이다).
   `circle-exclamation` 은 `<circle r=10/>` 하나뿐이라 **느낌표가 없다**
   (`Empty variant="error"` · `Toast tone="error"` · `MinWidthNotice` 가 쓴다).

### ① 프로젝트가 바뀌었다

| | 전 | 후 |
|---|---|---|
| `projectId` | `8fa54d76-58d6-481a-aed9-743dff9d6c09` (구 `SCAX`) | **`7e839512-977c-4142-b4b5-d992df566ffc`** (`TheSC AX Design System`) |

구 프로젝트에는 **아무것도 쓰지 않는다.** 구 20종이 거기 남아 있지만 이제 소스가 없는 목록이다.

### ② 부품이 `src/*.tsx` → `src/ds/*.tsx` 로 갔다 (바퀴 10)

`src/` 가 도메인 구조(`ds/` · `features/` · `shell/` · `lib/` · `styles/`)로 갈라지면서 **경로가 전부 바뀌었다**
(`git mv` 118건). `componentSrcMap` 20개가 전부 죽은 경로였고 `ds-entry.tsx` 도 마찬가지였다.

- 디자인 부품은 **`src/ds/` 한 곳**에 산다. 비-테스트 `.tsx` 22개 + `ds/icons/Icon.tsx`.
- `Icon` 은 `src/ds/icons/Icon.tsx` 다(표는 그 옆 `glyphs.tsx`).
- **`TaskCalendar` 는 이제 디자인 부품이 아니다.** `features/work/WorkViews.tsx` 로 갔다 —
  목록에서 뺐고 프리뷰도 지웠다. 다시 넣으려면 `ds/` 로 옮기는 것이 먼저다.
- `Composer` 는 `features/chat/Composer.tsx` 다 — 채팅 화면 부품이라 `ds/` 밖이고, 여기 목록에 없다.
  (`.scax-composer` **클래스**는 `styles/components.css` 에 있으니 CSS 는 간다.)

### ③ `cssEntry` 가 바뀌었다 — 구 파일은 **삭제됐다** (바퀴 9-B)

| | 전 | 후 |
|---|---|---|
| `cssEntry` | `src/styles.css` (1313줄, **지금은 없는 파일**) | **`src/styles/index.css`** |

`index.css` 는 값을 스스로 갖지 않고 **14벌을 순서대로 `@import`** 하는 묶음이다
(`fonts` → `fig-tokens` → `fig-typography` → `typography` → `product` → `scax` → `shell` → `components` →
`workspace` → `meetings` → `screens-a` → `scrollbar` → `screens-b` → `ax`).
토큰 이름도 전부 `--scax-*` 로 갈렸다 — 구 `--text-tertiary` · `--action` · `--on-fill` 같은 이름은 **없다**.
프리뷰가 인라인 스타일에서 그 이름을 쓰고 있으면 색이 통째로 빠진다(구 `Icon.tsx` 프리뷰가 그랬다).

### ④ 부품이 **말을 모른다** — 이번 프리뷰 작성의 함정 (바퀴 11)

**`src/ds/` 안에는 한국어가 0이다.** 화면에 나가는 문구는 전부 prop 으로 올라왔다. 앱에서는
`src/lib/labels.ts` 가 그 한 벌을 들고 있지만, **번들 엔트리는 디자인 부품만 내보내므로 프리뷰가
호출부로서 같은 값을 직접 들고 있어야 한다.** 안 넘기면 컴파일이 안 되거나 빈 화면이 찍힌다.

문구를 새로 받게 된 부품 (구 `dtsPropsFor` 가 전부 틀렸던 자리):

| 부품 | 새로 **필수**가 된 것 |
|---|---|
| `Skeleton` | `label` (전엔 선택) |
| `ProgressBar` | `ariaLabel` (신규) |
| `IconButton` | `label` |
| `Drawer` · `Modal` | `closeLabel` (신규) |
| `ConfirmModal` | `cancelLabel`(`string \| null`) · `closeLabel` (신규) |
| `Toast` | `closeLabel` (신규) |
| `SegmentedControl` · `Tabs` | `ariaLabel` |
| `Select` · `MultiSelect` | `labels`(6키) · `emptyActionLabel` (신규) |
| `TimeField` | `labels`(6키) · `selectLabels`(6키) · `emptyActionLabel` (신규) |
| `TimeRangeField` | 위 셋 + `startLabel` · `endLabel` (신규) |
| `DatePicker` · `DateField` | `labels`(5키) · `weekdayNames`(7) · `formatMonth` · **`today`** (전부 신규) |
| `FileList` | 줄마다 `removeLabel` |
| `MinWidthNotice` | `title` · `description` (전엔 props 없음) |
| `Empty` | 행동이 있으면 `actionLabel` 도 — **타입이 둘을 한 쌍으로 묶는다**(`EmptyAction` 유니온) |

**`today` 가 특히 무섭다** — `DatePicker` 는 이제 시계를 읽지 않는다. 프리뷰가 `today` 를 안 주면
컴파일이 안 되고, 실제 시계를 주면 카드마다 다른 달이 열린다. 프리뷰는 **`"2026-09-14"` 로 고정**했다.

### ⑤ 부품 20 → **34종**

새로 목록에 든 것과 그 출처:

| 부품 | 어느 gap 을 채운 것인가 |
|---|---|
| `Avatar` | **G-43** — 이니셜 아바타. 새 DS 의 아바타는 `object-fit:cover` 인 «사진» 전제라 글자를 못 받는다 |
| `DataTable`(+`Th`·`Td`·`TrOpenable`) | **G-15 · G-46** — 열 수에 안 묶인 읽기 표. `.scax-task-table` 은 업무 전용 6열 격자다 |
| `Button`(+`ButtonGroup`·`IconButton`) | `inline` = **글줄 속 단추**(구 `.btn.link` 11자리) · `ai` = **G-17** AX 표면 |
| `Badge` | `outline` 톤 = **G-29** — 「아직 확정 아닌 값」. 채움형 neutral 과 뜻이 다르다 |
| `Chip`(+`ChipToggle`) | 구 `.filter-chip` · `.chip-toggle` 을 부품으로. `ChipBar`·`ChipRow` 는 배치 전용이라 카드로 세우지 않았다 |
| `SegmentedControl` · `Tabs` | 구 `.segmented` · `.page-tabs` 를 부품으로 |
| `StatusNote` | **G-25** — 인라인 상태 한 줄. `Toast` 는 4초 뒤 사라져 「지금 상태」를 못 말한다 |
| `GutterList` | **G-26** — 2단 메타 목록(원문 · 활동 기록) |
| `DropZone` · `FileList` | 파일 받는 자리 · 파일 목록 (SPEC-004 §10) |
| `Modal` | 가운데 모달 — `Drawer`(G-10 으로 `sm`/`lg` 두 단)와 별개 부품이다 |

빠진 것: **`TaskCalendar`**(②). 엔트리에는 있지만 카드로 세우지 않은 것:
`ChipBar` · `ChipRow` · `AvatarEmpty` · `Th` · `Td` · `TrOpenable` · `CheckboxBox` · `SelectOptionPanel` ·
`useEscape` · `formatTime`/`parseTime`/`timeSlots` — 배치·내부 부품·헬퍼다.

### ⑥ 폰트 얼굴이 둘이 됐다

앱은 더 이상 시스템 설치 폰트에 기대지 않는다. `styles/fonts.css` 가 `@font-face` 두 벌을 직접 들고
`/fonts/*.woff2`(= `frontend/public/fonts/`)를 가리킨다. **그 절대 경로는 렌더 환경에서 안 뜬다.**

- `--font-ui`(본문·UI **전부**) = **"Pretendard JP"** · `--font-display`(표지급) = "Pretendard"
- 지난 동기화의 `.design-sync/fonts/` 에는 **비-JP 하나뿐**이었다 — 그대로 두면 UI 전체가 시스템 sans 로 떨어진다.
- 그래서 `PretendardJPVariable.woff2`(5.3MB)를 `.design-sync/fonts/` 로 **복사**하고 `pretendard.css` 에
  `@font-face` 를 더했다. 리포에 같은 바이너리가 두 벌 생긴다 — 렌더 환경이 `frontend/public/` 을
  상대 경로로 따라갈 수 있다면 이 사본은 지워도 된다(확인 못 했다).
- `DS-gaps` **G-01**(「글꼴이 Pretendard JP 가 맞나」)이 아직 사용자 판단 대기다. 여기서는 **앱이 실제로
  쓰는 것**을 그대로 실었다 — 프리뷰가 다른 얼굴로 찍히면 그 판단을 할 근거가 없어진다.

### ⑦ 프리뷰가 쓰던 셀렉터·클래스가 죽어 있었다

- `Opened` 래퍼가 `root.querySelector(".popover")` 로 열림을 판정했다. 바퀴 3a 에서 패널이
  **`.scax-popover`** 가 됐다 — 고치지 않으면 래퍼가 400ms 마다 트리거를 다시 눌러 카드가 깜빡인다.
  (`Popover.tsx` 프리뷰만 `aria-expanded` 로 물어서 무사했다.)
- `Skeleton` 프리뷰의 `NoDelay` 우회(`.skeleton-bar{animation-delay:0s}`)는 **필요 없어졌다** —
  새 `.scax-skeleton` 에는 `animation-delay` 가 없다. 지웠다.
- 구 `Icon` 프리뷰의 글리프 이름 26개 중 **6개가 사라졌다**:
  `check-square`→`square-check` · `list`→`list-category` · `refresh`→`reset` ·
  `alert`→`circle-exclamation` · `empty`→`inbox` · `filter`→`tune`. 지금은 35종이다.
- 구 `.btn` · `.btn primary` · `.btn ai` · `.btn icon` 은 **없다**. `Button`/`IconButton` 부품을 쓴다.
- `.plain-table`/`.title-cell` 은 아직 CSS 에 살아 있지만 읽는 표의 정본은 `DataTable`(`.scax-table`)이다.

### ⑧ 프리뷰 타입 검사를 붙여 뒀다 — **업로드 전에 반드시 돌려라**

`dtsPropsFor` 와 프리뷰가 소스와 어긋나도 아무것도 경고하지 않는 것이 이 리포의 고질병이다.
그래서 프리뷰를 실제 `ds-entry.tsx` 타입에 물리는 tsconfig 를 뒀다:

```
cd frontend && npx tsc -p ../.design-sync/tsconfig.previewcheck.json   # → 0
```

`ax-workspace-frontend` 를 `ds-entry.tsx` 로, `react` 를 `frontend/node_modules/@types/react` 로
매핑한 것뿐이다(프리뷰가 `frontend/` 밖에 있어 그냥은 `react` 를 못 찾는다).
준비 과정에서 이 검사가 프리뷰 34종을 **0 에러**로 세웠다 — 문구 prop 을 새로 받게 된 부품 14종이
전부 여기서 걸린다(안 넘기면 컴파일이 안 된다).

앱 쪽 검사는 그대로다:
```
cd frontend && npx tsc --noEmit     # → 0   (단, tsconfig 의 include 는 ["src"] 라 ds-entry 를 안 본다)
```
`ds-entry.tsx` 는 위의 previewcheck 가 같이 본다 — 앱 tsc 만으로는 엔트리가 검사되지 않는다는 뜻이다.

---

## 리포 형태 (그대로인 것)

- 컴포넌트 라이브러리가 아니라 Vite 앱(`frontend/`)이다. 라이브러리 `dist/` 가 없어서
  `cfg.entry = ./frontend/ds-entry.tsx` 를 따로 두고 디자인 부품만 내보낸다. 소스 합성 모드(synth-entry)로
  두면 `src/**` 전부(페이지·api·sigma 그래프)가 번들에 들어가므로 쓰지 않는다.
- 부품이 늘면 `frontend/ds-entry.tsx` 와 `cfg.componentSrcMap` 에 **같이** 추가한다.
  `componentSrcMap` 이 곧 컴포넌트 목록이다(.d.ts 가 없어 자동 발견이 안 된다).
- 컴포넌트는 전부 CSS 클래스 위의 얇은 래퍼라 provider 가 없다.
- `cfg.readmeHeader` = `.design-sync/conventions.md`. **이 문서도 2026-09-14 에 통째로 다시 썼다** —
  구 어휘(`btn`·`badge ai`·`plain-table`·`avatar xs`)를 가르치고 있었다.

## 렌더 검사 환경

- playwright chromium 캐시는 macOS 에서 `~/Library/Caches/ms-playwright/` 다(`~/.cache/` 아님).
- **새 워크트리에는 `frontend/node_modules` 가 없다.** `cd frontend && npm ci` 를 먼저 돌린다 —
  안 하면 `npx tsc` 가 엉뚱한 패키지를 받아 오고 playwright 심볼릭 링크도 못 만든다.
- `.ds-sync/node_modules/playwright` 는 `frontend/node_modules/playwright` 로의 심볼릭 링크다 —
  새 클론에서는 다시 만든다.

## 프리뷰

- `.design-sync/previews/<Name>.tsx` **34개**, 컴포넌트와 1:1 이다. 짝 없는 공유 모듈을 두지 않는다
  (컨버터가 대문자 named export 를 카드 셀로 세므로 `_labels.tsx` 같은 파일은 짝이 없어 위험하다 —
  문구 한 벌은 각 파일이 직접 들고 있는다).
- `Popover`·`DatePicker`·`Select`·`MultiSelect`·`TimeField` 는 열린 상태를 props 로 강제할 수 없어
  프리뷰 안의 `Opened` 래퍼가 마운트 직후 트리거를 `click()` 하고, 400ms 마다 닫힘을 감지해 다시 연다
  (제품 카드가 살아 있는 iframe 이라 사용자가 항목을 누르면 닫혀 버렸던 것의 대응, 2026-09-07 반려).
- `Popover` 는 열린 패널이 셀 밖으로 나가 잘렸다(2026-09-07 사용자 보고) →
  `cardMode: single` + `primaryStory: StatusFilter`. Closed 스토리는 `?story=Closed` 로만.
- 카드 폭 함정: `TimeRangeField` 의 `WithDate` 를 560px 로 두면 종료 필드가 **잘린다**.
  스토리 500 + `overrides.TimeRangeField.viewport = 620x520` 으로 맞췄다.
- 툴바 트리거 스토리(`Select` `ChipTrigger`)에는 `filter-chip` 에 `Icon name="chevron-down" size={12}` 를
  반드시 같이 둔다 — 가이드의 툴바 골격이 그 모양이라, 빠지면 카드가 규약과 어긋난 예시를 가르친다.
- `MinWidthNotice` 는 1280 미만 미디어쿼리에서만 보여서 카드 뷰포트를 960x480 으로 둔다.
  `Drawer`/`Modal`/`ConfirmModal`/`Toast` 는 position:fixed 라 `cardMode: single` + 뷰포트.
- 텍스트가 긴 스토리는 `[GRID_OVERFLOW]` 가 떠서 `cardMode: column` 으로 돌린다.
  이번에 새로 `column` 으로 둔 것: `Avatar`·`Badge`·`Button`·`IconButton`·`Chip`·`DataTable`·
  `GutterList`·`FileList`·`DropZone`.
- package-capture 의 리뷰 시트는 시계를 2024-05-15 로 고정해 찍는다. 「오늘」에 기대는 스토리는
  리뷰 시트에서 날짜가 이상해도 정상 — validate 의 카드 스크린샷(`_screenshots/general__<Name>.png`)에서 본다.
  (④ 때문에 이제 「오늘」은 프리뷰가 `today="2026-09-14"` 로 직접 고정한다.)

## Known render warns (2026-09-14 새 프로젝트에서 재확인 — 둘 다 non-blocking)

- `[GRID_OVERFLOW] Toast (fixed/portal)` — column 카드에서 각 셀이 transform 을 가져 fixed 토스트가
  셀 안에 갇힌다. **리뷰 시트에서 4셀(Basic·Error·Success·WithAction) 전부 정상 렌더 확인(2026-09-14).**
  single 로 바꾸면 Success/Error 가 안 보여 사용자가 반려했으므로 column 유지.
- `[TOKENS_MISSING] --action · --scax-space-1000 · --scax-space-350 · --days` —
  `--days` 는 캘린더가 인라인 style 로 넣는 런타임 변수라 **정상**(경고 문구 자체가 그렇게 말한다).
  나머지 셋은 **정의되지 않은 채 참조되는 실제 죽은 참조**다: `--action` 은 은퇴한 구 DS 토큰 이름이고
  `--scax-space-1000`·`--scax-space-350` 은 space 램프에 없는 값이다(있는 것은 …800·1200). 앱 쪽 정리 대상.

## Re-sync risks

- **`dtsPropsFor` 는 수동 복사본이다.** 소스 시그니처가 바뀌어도 아무것도 경고하지 않는다.
  이번(2026-09-14)에 34종 전부를 소스와 대조해 다시 썼고, prop 이름 집합·필수/선택 표시가
  34/34 일치함을 스크립트로 확인했다. **다음에도 반드시 대조한다.**
- **`ds-entry.tsx` 와 `componentSrcMap` 은 손으로 맞춘 목록이다.** `ds/` 에 새 파일이 생겨도 자동으로 안 올라간다.
- **`frontend/src/styles/` 가 16개 파일로 갈라져 있다(`index.css` 가 그중 14벌을 묶는다).** 어느 한 벌만 바뀌어도 `index.css` 를 통해 번들에
  들어온다 — CSS 진단은 `index.css` 가 아니라 그 아래 파일을 봐야 한다.
- **아직 안 닫힌 gap 둘**: **G-05**(`FieldMessage` 의 `warning`·`info` 톤 — 부르는 자리가 0곳이라
  만들지 않았다) · **G-45**(폼용 여러 줄 입력 — `.scax-textarea` 클래스는 있으나 React 부품이 없다).
- **사용자 판단이 남은 것**: G-35(「요청」 배지가 주황→보라로 **뜻이 바뀌었다**) · G-06(그래프 15색이
  「악센트 하나」 원칙과 충돌) · G-01/G-02(글꼴 얼굴과 로컬 동봉) · G-14(칸반·타임라인 유지 여부).
  DS 에 올리기 전에 이 넷은 사람이 정해야 한다.
- durable 파일(`.design-sync/`·`frontend/ds-entry.tsx`)은 **커밋하지 않은 채** 남긴다 —
  커밋은 코디네이터/사용자 판단.
