# TheSC AX 로 화면을 지을 때 — 규약

TheSC AX 는 **CSS 클래스 + 시맨틱 토큰** 체계다. 래퍼·Provider 없음, CSS-in-JS 없음, 유틸리티 클래스 없음.
`src/styles/index.css` 하나를 링크하면 토큰·컴포넌트 스타일·Pretendard 가 전부 온다.
React 부품은 **35종**(`window.SCAX.*`)이고, 나머지 부품은 아래 클래스 어휘를 그대로 쓴 일반 HTML 이다.
직접 CSS 를 새로 쓰기 전에 이 표에 있는지 먼저 본다.

> **2026-09-14 에 이 문서가 통째로 다시 쓰였다.** 앱이 구 디자인 시스템을 은퇴시키고 TheSC AX 로 옮겼다
> (PR #12). 구 어휘(`btn` · `badge ai` · `plain-table` · `avatar xs` · `styles.css`)는 **이제 없다.**

## 부품 35종 — `window.SCAX.*`

| 갈래 | 부품 |
|---|---|
| 글리프 | `Icon`(38종 — 24그리드 29 · 16그리드 8 · 면 1) |
| 사람 | `Avatar`(5단) |
| 표시 | `Badge`(6톤) · `StatusNote` · `ProgressBar` · `TimeChip` · `EmptyValue` |
| 단추 | `Button`(5변형) · `ButtonGroup` · `IconButton` |
| 고르기(비폼) | `Chip` · `ChipToggle` · `SegmentedControl` · `Tabs` |
| 목록·표 | `DataTable`(+`Th`·`Td`·`TrOpenable`) · `GutterList` · `FileList` |
| 폼 | `Checkbox` · `FieldMessage` · `Select` · `MultiSelect` · `DateField` · `DatePicker` · `TimeField` · `TimeRangeField` · `DropZone` |
| 오버레이 | `Popover` · `Drawer` · `Modal` · `ConfirmModal` · `Toast` |
| 상태 | `Empty` · `Skeleton` · `Spinner` · `MinWidthNotice` |

### 부품은 **말을 모른다** (바퀴 11)

`src/ds/` 안에는 **한국어가 한 글자도 없다.** 화면에 나가는 문구는 전부 prop 으로 들어온다 —
닫기 단추의 이름도, 「오늘」도, 요일 머리글도, 「조건에 맞는 항목이 없습니다」도.
앱에서는 `src/lib/labels.ts` 가 그 한 벌을 들고 있고, 부르는 쪽이 그것을 그대로 펼쳐 넘긴다.

그래서 **말을 안 넘기면 컴파일이 안 되는** 부품이 여럿이다:

| 부품 | 반드시 넘겨야 하는 문구 |
|---|---|
| `Skeleton` · `Spinner` | `label` |
| `ProgressBar` | `ariaLabel` (머리줄이 없어도 막대는 읽혀야 한다) |
| `IconButton` | `label` |
| `Drawer` · `Modal` | `closeLabel` |
| `ConfirmModal` | `cancelLabel`(`null` 이면 × 로 대체) · `closeLabel` |
| `Toast` | `closeLabel` |
| `SegmentedControl` · `Tabs` | `ariaLabel` |
| `Select` · `MultiSelect` | `labels`(6개) · `emptyActionLabel` |
| `TimeField` · `TimeRangeField` | `labels`(6개) · `selectLabels`(6개) · `emptyActionLabel` (+ 한 쌍은 `startLabel`·`endLabel`) |
| `DatePicker` · `DateField` | `labels`(5개) · `weekdayNames`(7) · `formatMonth` · **`today`** |
| `FileList` | 줄마다 `removeLabel` |
| `DropZone` | `drop`(머리 한 줄) · `pickLabel` · `hint` |
| `MinWidthNotice` | `title` · `description` |
| `Empty` | 행동이 있으면 `actionLabel` 도 — **타입이 둘을 한 쌍으로 묶는다** |

「오늘」도 마찬가지다 — `DatePicker` 는 시계를 읽지 않는다. 어느 시간대의 오늘인지는 부르는 쪽이 안다.

## 클래스 어휘 (`src/styles/` 에 실재하는 이름만)

| 자리 | 클래스 |
|---|---|
| 단추 | `scax-button` + `--solid-primary` / `--solid-danger` / `--outlined-primary` / `--outlined-neutral` / `--text-neutral` / `--inline` / `--ai`, 크기 `--sm`·`--lg`, 줄 폭 `--block` · `scax-button-group` · `scax-icon-button`(28px, `--star-on`) |
| 배지 | `scax-badge` + `--accent` / `--neutral` / `--info` / `--positive` / `--danger` / `--outline` / `--count` |
| 칩 | `scax-chip`(+`--on`) · `scax-chip-bar`(툴바, `__end`) · `scax-chip-row`(줄바꿈) · `scax-chip-toggle`(+`--on`, 폼 값) |
| 고르기 | `scax-segmented`(칸 안, `__item--on`) · `scax-tabs`(면을 가르며, min-width 880) |
| 아바타 | `scax-avatar` + `--xs`(20) `--sm`(24) `--md`(32) `--lg`(40) `--xl`(80) |
| 표 | `scax-table`(읽는 표, 열 수 자유 · `__center`/`__end`/`__title`/`__row--openable`) · `scax-task-table`(업무 전용 6열 격자) |
| 목록 | `gutter-list`/`gutter-row`(+`active`)/`gutter-meta`/`gutter-aside`/`gutter-body`(+`muted`) · `scax-file-list`/`scax-file-row`(+`active`, `__name`·`__size`, `file-open`) |
| 폼 | `scax-field`(`__label`·`__hint`·`__error`·`__required`) · `scax-field-row` · `scax-textfield` · `scax-textarea` · `scax-checkbox`(`__input`·`__box`) · `scax-select` · `scax-dropzone`(+`--over`) · `field`(구 골격, 아직 산다) |
| 오버레이 | `scax-popover`(+`--above`, 포털로 body 에 선다) · `scax-drawer`(+`--sm` 520 / `--lg` 840, `scax-drawer-overlay`) · `scax-modal`(+`--sm`/`--md`, 기본 880, `scax-modal-overlay`) · `scax-toast` |
| 상태 | `scax-spinner`(+`scax-spinner-row`) · `scax-empty`(`__icon`·`__title`·`__desc`) · `scax-status-note`(못 불러온 자리 — 「비었다」가 아니다) · `scax-skeleton`(+`--text`/`--title`/`--card`, `scax-skeleton-stack`) · `status-note`(인라인 한 줄) · `min-width-notice` |
| 시각 | `scax-time-chip` |
| 셸 | `scax-app-shell` · `scax-app-main` · `scax-page-header` · `scax-page-body` · `scax-side-nav`(+`--collapsed`) · `scax-nav-item`(+`--active`) · `scax-breadcrumb` |
| 텍스트 | `t-item` · `t-meta` · `tabular` · `danger-text` · `sr-only` · `.ax-*` 타이포 램프 10종 |
| 화면 골격 | `surface-card` · `card-title` · `meta-grid` · `page-head`(+`page-head-actions`) · `section-title` · `toolbar`(+`toolbar-group`) · `drawer-section` · `spacer` |

## 토큰 — 시맨틱 이름으로만 (`var(--scax-…)`)

화면 CSS 는 `--scax-*` 만 참조한다. 그 아래 `--ax-*`(제품 별칭) → `--*`(Figma 원시 토큰) 층이 있지만
화면이 그 이름을 직접 부르지 않는다.

- 글자: `--scax-color-ink`(본문) · `--scax-color-ink-strong` · `--scax-color-ink-neutral` · `--scax-color-ink-alt` · `--scax-color-ink-assistive`(메타) · `--scax-color-ink-disabled` · `--scax-color-ink-inverse`(채운 면 위)
- 선 3단: `--scax-color-line-strong`(외곽) · `--scax-color-line`(내부) · `--scax-color-line-weak`(행 구분)
- 면: `--scax-color-surface` · `--scax-color-surface-alt` · `--scax-color-surface-selected` · `--scax-color-fill`(+`-weak`/`-strong`) · `--scax-color-popover-current`
- 강조: `--scax-color-accent`(액션과 **완료** 에만) + `-05`/`-08`/`-20`/`-soft`/`-strong`
- 상태: `--scax-color-danger`(+`-soft`) · `--scax-color-positive` · `--scax-color-info` · `--scax-color-warning`(+`-soft`/`-ink`) · `--scax-color-progress`(+`-soft`/`-ink`)
- AX 표면(`DS-gaps` G-17): `--scax-color-ai-ink` · `-ink-active` · `-line` · `-line-hover` · `-line-active` · `-fill-hover` · `-fill-active` (7종)
- 관계 그래프(`DS-gaps` G-06): `--scax-graph-*` 15종(노드 9 · 엣지 4 · 라벨 2) — **「악센트 하나, 나머지 중립」 원칙과 정면으로 부딪히는 자리다. 예외로 공인할지는 아직 사용자 판단 대기.**
- 치수: `--scax-space-*` · `--scax-radius-*`(`xs`·`sm`·`md`·`lg`·`xl`·`2xl`·`chip`·`pill`·`panel`·`bubble`) · `--scax-control-height-*`(`sm`·`md`·`lg`) · `--scax-text-*`(크기·행간·자간) · `--scax-fw-*`
- 글꼴: `--font-ui` = **"Pretendard JP"**(본문·UI 전부) · `--font-display` = "Pretendard"(표지급 큰 글자)

## 하지 않는 것

- **네이티브 `<select>` · `input[type=date]` · `input[type=time]` 을 쓰지 않는다.** 셋 다 브라우저·로캘에
  따라 모양과 글자 순서가 갈린다. 각각 `Select`/`MultiSelect` · `DateField` · `TimeField` 가 대신한다.
  이 셋의 트리거는 전부 같은 컨트롤 규격(h34 · 14px)이라 한 줄에 나란히 선다.
- **초록을 쓰지 않는다.** 완료는 `--scax-color-accent` 다.
- **드로어 위에 모달을 겹치지 않는다.**
- **부품 안에 문구를 박지 않는다.** 위의 「부품은 말을 모른다」 참조.
- **`Empty` 의 `error` 를 「비어 있음」으로 읽지 않는다.** 못 불러온 것은 `.scax-status-note` 로 그려진다.

## `Skeleton` 과 `Spinner` 는 갈래가 다르다

갈리는 축은 **「올 것의 모양을 아는가」** 다. 목록처럼 줄 수와 높이를 아는 자리는 `Skeleton`
(실제와 같은 개수로, 리스트는 5행). 합성이 끝난 회의록처럼 **몇 줄이 올지 모르는** 자리는
`Spinner` — 거기에 막대를 까는 것은 「모양을 아는 척」이다.

## 화면은 네 상태를 같이 그린다

정상 · 로딩(`Skeleton`, 실제 행 수만큼 — 리스트는 5행) · 비어 있음(`Empty` `variant="default"|"filter"`) ·
실패(`Empty` `variant="error"` 또는 `StatusNote tone="danger"`).
