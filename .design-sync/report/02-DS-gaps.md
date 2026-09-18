# 02 — DS gaps: 시안이 쓰는데 우리 DS 에 없는 것

- **시안(A)**: `.design-sync/screens/` — `handoff/**/*.jsx` · `handoff/my-work/css/components.css`
- **우리 DS**: `.design-sync/config.json` 의 `componentSrcMap` **35종** + `frontend/src/styles/**` 클래스 **460종** + `--scax-*` 토큰 **143종** + 글리프 **40종**(`frontend/src/ds/icons/glyphs.tsx`)
- 차집합만 적는다. 「무엇을 만들어라」는 적지 않는다.

---

## 0. 전수 — 센 수치

| # | 무엇 | 수 |
|---|---|---|
| 1 | 현재 업무 화면의 표면 (`features/work` export) | 33 |
| 2 | 시안이 쓰는 DS 표면 | 클래스 **179**종 / JSX 클래스 토큰 70 / **`window.SCAX_DS.*` 호출 1종(`Icon`)** / 글리프 이름 **28**종 / React 부품 **44**종 |
| 3 | 우리 DS 표면 | 부품 **35** / 클래스 **460** / 토큰 **143** / 글리프 **40** |
| 4 | 시안이 그린 행위값 | status 5 · STATUS_OPTIONS 3 · badge 4 · tone 5 · actions 2 |

---

## A. CSS 클래스 — **차집합 1종**

시안 `components.css` 의 179종 중 **178종이 이미 우리 어휘에 있다.** 없는 것은 하나뿐이다.

| 없는 클래스 | 시안에서의 쓰임 | 근거 |
|---|---|---|
| `.scax-select__caret` | 상태 Select 트리거 오른쪽의 CSS 삼각형 캐럿 | 정의 `handoff/my-work/css/components.css:42`, 사용 `handoff/shell/js/scax-ui.jsx:90` |

- 우리 `Select` 는 캐럿을 **아이콘으로** 그린다: `frontend/src/features/work/MyWorkPage.tsx:802` 의 `<Icon name="chevron-down" size={12} />`.
  같은 그림을 두 방법으로 그리고 있는 것이고, 어느 쪽이 정본인지는 정해져 있지 않다 → **OQ-DS-01**.

> 참고: `.scax-scroll` 은 `frontend/src/styles/components.css` 에는 없지만 `frontend/src/styles/scrollbar.css` 에 있다 — 차집합 아님.

## B. 디자인 토큰 — **차집합 0종**

시안이 `var(--scax-*)` 로 부르는 토큰 **79종이 전부 정의돼 있다.** 빠진 토큰 없음.

## C. 아이콘 글리프 — **차집합 9종** ★실질 gap

시안이 부르는 글리프 28종 중 **9종이 `glyphs.tsx` 40종 안에 없다.**

| 없는 글리프 | 시안에서의 자리 | 근거 |
|---|---|---|
| `mail` | 수신함 카드의 출처 아이콘 · 업무 만들기 모달의 출처 행 | `handoff/my-work/js/data.js:20` · `:163` |
| `message` | 수신함 카드의 출처(메신저) | `data.js:45` |
| `external-link` | 수신함 카드의 「원문으로」 표시 | `handoff/my-work/js/my-work.v2.jsx:49-52` |
| `circle-close` | TextField·AutoComplete 의 지우기 단추 | `handoff/shell/js/work-modal.jsx:55` · `:100` |
| `circle-check` | 「완료 업무」 빈 상태 아이콘 | `my-work.v2.jsx:207` |
| `chevron-left-small` | 캘린더 이전 | `my-work.v2.jsx:290` |
| `chevron-right-small` | 캘린더 다음 | `my-work.v2.jsx:291` |
| `chat` | 좌측 내비 「채팅」 | `handoff/shell/js/nav.js:14` |
| `image` | 첨부 목록의 이미지 파일 | `data.js:182` |

우리가 가진 가장 가까운 것: `chevron-right`·`chevron-down`(있음, `-small` 변형 없음, **`chevron-left` 자체가 없다**),
`circle`·`circle-exclamation`(있음, `circle-check`/`circle-close` 없음), `link`(있음, `external-link` 없음), `close`(있음).

## D. React 부품 — 이름 기준 **차집합 26종**

시안이 정의하는 부품 44종 − DS 35종 = **26종**. 셋으로 갈린다.

### D-1. DS 에 없고 **앱에도 없는** 17종 — 진짜 새 부품

`AgendaItem` · `AgentBubble` · `AutoComplete` · `CalendarNav` · `Checklist` · `DayCell` · `DoneTaskTable` ·
`Field` · `MonthView` · `SentTaskTable` · `StateSwitch` · `TableState` · `TaskCreateModal` · `TaskPanel` ·
`TaskTable` · `TextField` · `WeekView`

- 단, **CSS 는 이미 있다** — 위 A 절대로 클래스 차집합이 1종뿐이라, 이 17종은 「스타일이 없다」가 아니라
  **「React 부품으로 묶여 있지 않다」** 는 뜻이다. 예: `.scax-checklist*` 6종·`.scax-autocomplete*` 4종·
  `.scax-textfield*` 6종·`.scax-day-cell*` 5종·`.scax-month-grid*`·`.scax-day-section*` 4종이 모두
  `frontend/src/styles/components.css` 에 서 있다.
- `AgentBubble` 만 예외로 앱에 대응물이 있다 — `frontend/src/features/assistant/AssistantCharacter.tsx:64-90`
  (이름이 달라 위 목록에 들었다).
- `StateSwitch` 는 시안 스스로 **dev 전용**이라고 못박았다: `handoff/shell/js/scax-ui.jsx:293`
  「Dev-only. Not part of the product UI — remove when implementing.」 → DS gap 으로 세지 않는 것이 맞아 보인다. **OQ-DS-02**

### D-2. DS 35 에는 없지만 **앱에 같은 이름이 있는** 8종 — 「DS 로 승격 안 된 것」

| 부품 | 앱의 자리 |
|---|---|
| `AppShell` · `AppHeader` · `AppBody` | `frontend/src/shell/AppShell.tsx` |
| `SideNav` | `frontend/src/shell/SideNav.tsx` |
| `InboxRail` · `InboxCard` | `frontend/src/shell/InboxRail.tsx` |
| `CalendarRail` | `frontend/src/shell/CalendarRail.tsx` |
| `MyWorkPage` | `frontend/src/features/work/MyWorkPage.tsx` (화면이지 부품이 아니다) |

시안 자신이 이 상태를 알고 있다 — `handoff/shell/js/scax-ui.jsx:201-205`:
「현재 DS 번들(`_ds_bundle.js`)에는 shell 부품이 없다 — 규칙(`.scax-app-shell` 외)은 `_ds_bundle.css` 에
그대로 있으므로 여기서 그 규칙대로 조립한다. 다음 design-sync 때 `components/shell/` 이 올라오면 이 블록을
지우고 DS 것을 쓴다.」 → **shell 4종을 DS 로 올릴지가 미정** → **OQ-DS-03**

### D-3. 이름이 겹치지만 **뜻이 다른** 1종

- `Composer` — 시안은 **여러 줄 입력 + 글자 수 카운터**(`work-modal.jsx:72-81`), 앱의 `frontend/src/features/chat/Composer.tsx` 는
  **채팅 입력창**이다. 같은 이름, 다른 물건. DS 이름 충돌이 된다 → **OQ-DS-04**

### D-4. DS 35 중 시안이 **안 쓰는** 17종 (참고)

`Avatar` · `Checkbox` · `ChipToggle` · `ConfirmModal` · `DataTable` · `DatePicker` · `Drawer` · `EmptyValue` ·
`FieldMessage` · `FileList` · `MinWidthNotice` · `MultiSelect` · `ProgressBar` · `Spinner` · `TimeChip` · `TimeField` · `TimeRangeField`

- 「없애라」는 뜻이 아니다. 이 화면 하나에 안 나온다는 사실일 뿐이다.
- 다만 `Checkbox`·`FileList` 는 **시안이 클래스로는 쓰는데 DS 부품으로는 안 부른다**:
  `.scax-checkbox__box` (`work-modal.jsx:128`), `.scax-file-row` (`work-modal.jsx:164`).
  즉 시안은 DS 부품 대신 마크업을 직접 썼다.

## E. 부품 API 차이 — 이름은 같은데 시그니처가 다른 것

| 부품 | 시안 API | 우리 API | 근거 |
|---|---|---|---|
| `Skeleton` | `{variant, width}` — 막대 **하나** | `{rows, label}` — 목록 **한 벌** | 시안 `scax-ui.jsx:197`; 우리 `frontend/src/ds/Skeleton.tsx:17`. 우리 주석(`Skeleton.tsx:6-8`)이 이 차이를 이미 적고 「필요해지면 variant 를 prop 으로 열면 된다」고 둔다 |
| `Empty` | `{icon, title, desc}` — 호출부가 아이콘을 고른다 | `{variant, title, description, actionLabel, onAction}` — `variant` 가 아이콘을 정한다 | 시안 `scax-ui.jsx:176`; 우리 `frontend/src/ds/Empty.tsx:34-50` |
| `StatusNote` | `{tone, title, desc, action, inline}` | 우리 `Empty(variant="error")` 가 `.scax-status-note` 를 그린다 + 별도 `ds/StatusNote.tsx` | 시안 `scax-ui.jsx:186`; 우리 `Empty.tsx:52` |
| `Select` | `{value, options, tone, onChange, ariaLabel}` + 내장 캐럿 | `{value, options, onChange, label, labels, trigger}` — 트리거를 호출부가 끼운다 | 시안 `scax-ui.jsx:74`; 우리 `frontend/src/ds/Select.tsx:353-380` |
| `GutterList` | `{title, count, headerEnd, children}`, 아이콘이 `inbox` 로 **고정** | 우리 `ds/GutterList.tsx` (`InboxRail` 이 직접 마크업으로 씀) | 시안 `scax-ui.jsx:150-152` |
| `Badge` | `{tone, variant:"count", children}` | **같다** (`frontend/src/ds/Badge.tsx:24-38`), `tone:"info"` 도 양쪽에 있다 | 차이 없음 |
| `Chip` | `{label, on, onClick}` | **같다** (`frontend/src/ds/Chip.tsx:17-31`) | 차이 없음 |

## F. 시안이 DS 번들을 거의 안 쓴다 — 사실만

시안 12파일 전체에서 DS 번들 호출은 **`window.SCAX_DS.Icon` 하나뿐이다**
(`handoff/shell/js/scax-ui.jsx:13` · `:20`; 번들 자체는 `MyWork.html:17` 에서 `window.SCAX` 로 받는다).
나머지 43개 부품은 시안이 **직접 다시 그렸다.**

시안 자신의 설명은 「브라우저 Babel 이 파일별 스코프라 쪼개면 로딩 순서가 보장되지 않는다」(`scax-ui.jsx:5-7`)이다.
즉 **핸드오프 러너의 제약** 때문일 수 있고, 「DS 부품이 모자라서」가 아닐 수 있다.
이 둘을 가르는 것은 내가 판정할 일이 아니다 → **OQ-DS-05**

---

## Open Questions (02)

| # | 물음 |
|---|---|
| OQ-DS-01 | Select 캐럿 — CSS 삼각형(`.scax-select__caret`)인가 `<Icon name="chevron-down">`인가? 지금 둘이 공존한다 |
| OQ-DS-02 | `StateSwitch`(dev 전용)를 DS gap 으로 셀 것인가? 시안은 「구현 시 제거」라고 적었다 |
| OQ-DS-03 | shell 4종(`AppShell`/`AppHeader`/`AppBody`/`SideNav`) + 레일 3종을 DS 로 올릴 것인가? 시안이 「다음 design-sync 때」라고 남겼다 |
| OQ-DS-04 | `Composer` 이름 충돌 — 시안의 「여러 줄 입력+카운터」와 앱의 「채팅 입력창」 중 어느 쪽이 DS `Composer` 인가? |
| OQ-DS-05 | 시안이 DS 번들에서 `Icon` 만 쓴 것은 러너 제약인가, DS 부재인가? 답에 따라 D-1 의 17종 해석이 갈린다 |
| OQ-DS-06 | 글리프 9종을 추가할 것인가, 있는 것으로 대체할 것인가? (`chevron-left` 는 아예 없다) |
