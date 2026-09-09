# SCAX 로 화면을 지을 때 — 규약

SCAX 는 **CSS 클래스 + 시맨틱 토큰** 체계다. 래퍼·Provider 없음, CSS-in-JS 없음, 유틸리티 클래스 없음. `styles.css` 하나를 링크하면 토큰·컴포넌트 스타일·Pretendard 폰트가 전부 온다. 컴포넌트는 `window.SCAX.*` 19개뿐이고, 버튼·배지·테이블·카드·폼 같은 나머지 부품은 **아래 클래스 어휘를 그대로 쓴** 일반 HTML 이다. 직접 CSS 를 새로 쓰기 전에 이 표에 있는지 먼저 본다.

## 클래스 어휘 (styles.css 에 실재하는 이름만)

| 자리 | 클래스 |
|---|---|
| 버튼 5변형 | `btn`(secondary 기본) · `btn primary`(화면당 1개) · `btn ghost` · `btn danger` · `btn ai` · `btn link` |
| 버튼 높이·아이콘 | 기본 h34 · `h40` · `h30`(=`small`) · 아이콘만이면 `icon` (정사각형) |
| 상태 dot 5종 | `status` + `pending`/`in_progress`(진행 중, sky) · `done`/`accepted`(완료, indigo) · `blocked`(지연, red) · `cancelled`/`rejected` · 기본 = 시작 전 |
| 배지 (h20 r4) | `badge ai` · `badge neutral` · `badge progress` · `badge danger` · `badge outline` |
| 툴바 필터 칩 | `filter-chip`, 켜지면 `filter-chip on` (selected-bg + action) |
| 세그먼트·카운트 | `segmented` > `button[aria-selected]` · `count-badge`(검정 = 현재 위치) · `count-badge quiet`(폼 라벨 옆) · `checklist-progress` |
| 페이지 골격 | `page-head`(h1 + p + `page-head-actions`) · `toolbar` > `toolbar-group` · `section-title` |
| 패널·카드 | `surface-card`(r16 p24) · `card-title`(h3 + p) · `meta-grid`(dl > div > dt/dd, `columns` 변형) · `effect-note` |
| 테이블 | `plain-table`(th h44 / td h56, `title-cell`·`center`·`end`) · `work-table`(head + `progress-row`) · 빈 칸은 `<EmptyValue/>` |
| 폼 | `field`(label + input, gap 7) · `field-help` / `field-error` (→ `FieldMessage`) · `form-stack` · `title-input` · `search-input-box`(h34) · `aria-invalid="true"` 로 에러 보더. 날짜는 `DateField`(+`DatePicker`), 시각은 `TimeField` / `TimeRangeField`, 고르기는 `Select` / `MultiSelect` — 넷 다 트리거가 h34 · `--radius-control` · 14px 이라 한 줄에 나란히 선다 |
| 텍스트 | `t-item`(14 Bold) · `t-meta`(13 tertiary) · `danger-text` · `cancelled-title`(취소선) · `sr-only` |
| 아바타 | `avatar xs|sm|md|lg|xl` — 사이드바 프로필·홈 상단·드로어 헤더 세 곳에만 |
| 캘린더 | `TaskCalendar`(월 6주·주 7칸, 막대 = `calendar-chip` + 상태 클래스). 좌측 레일 260 은 v2 13 에 있으나 코드에 없다 |
| 오버레이 | 편집·상세 = `Drawer`(840) · 결정 하나 = `ConfirmModal`(600) · 고르기 = `Popover` · 날짜 고르기 = `DatePicker`(Popover 위 280) · 목록에서 고르기 = `Select`/`MultiSelect`/`TimeField`(Popover 위, 폭은 트리거에 맞춘 200–400) · 알림 = `Toast`(`tone="success"` check/accent · `tone="error"` alert/danger, 없으면 아이콘 없음). 드로어 위에 모달을 겹치지 않는다 |
| 상태 4종 | 데이터 화면은 정상 · 로딩(`Skeleton`, 실제 행 수만큼) · 비어 있음(`Empty`, variant default/filter/error) · 실패 를 같이 그린다 |

## 토큰 — 시맨틱 이름으로만 (`var(--…)`)

- 글자: `--text-primary`(Ink) · `--text-secondary` · `--text-tertiary`(Meta) · `--text-disabled` · `--on-fill`(채운 면 위 흰 글자)
- 보더 3단: 외곽 `--border-strong` · 내부 `--border-default` · 행 구분 `--border-subtle`
- 면: `--surface` · `--surface-sunken` · `--selected-bg`(선택 칩·카드) · `--surface-selected-row` · `--popover-current`
- 액션: `--action`(흰 글자를 얹는 primary) · `--action-hover` · `--action-active` · `--accent`(#7181F8, 액션과 완료에만) · `--focus-ring`
- 상태: 진행 `--progress-accent`/`--progress-text`/`--progress-tint` · 지연 `--danger`/`--danger-accent`/`--danger-tint`/`--danger-hover` · 시작 전 `--status-neutral-tint` · 경고 `--warning-surface`/`--warning-text`
- AI: `--ai-border` · `--ai-text` · `--ai-bg-hover` · `--shadow-ai`
- 라운드: `--radius-card`(8) · `--radius-panel`(16) · `--radius-control`(8) · `--radius-chip`(4) · `--radius-popover`(12)
- 그림자 6개뿐: `--shadow-sm` · `--shadow-md` · `--shadow-lg`(팝오버) · `--shadow-xl`(모달·토스트) · `--shadow-drawer` · `--shadow-ai`

규칙: **네이티브 `<select>` 와 `input[type=date|time]` 은 쓰지 않는다** — 브라우저가 OS 위젯으로 그려서 토큰이 닿지 않고, 표기가 로캘을 따라 갈라진다(같은 값이 사람마다 `2026/09/30`·`09/30/2026`, `14:30`·`오후 2:30`). 대신 `DateField` · `TimeField` · `TimeRangeField` · `Select` · `MultiSelect` 를 쓴다. 시각 표기는 24시간 `HH:MM` 고정이다. 검정(`--text-primary` 배경)은 현재 위치 표시에만, 화면당 하나 — 폼 라벨 옆 카운트는 `count-badge quiet`(글자 `--text-secondary`, 배경 `--surface-sunken`)를 쓴다. 선택은 `--selected-bg` + `--action`. 행간 145% · 자간 −2% 는 손대지 않는다(높이는 패딩으로). 간격 스케일 4·8·12·16·20·24·32·48. 새 그림자·새 배지 색을 만들지 않는다. 아이콘은 `Icon`(27종, 14/16/20) 만 쓰고 채운 아이콘을 만들지 않는다.

## 진실은 여기

- `styles.css` → `_ds_bundle.css`: 위 클래스와 `:root` 토큰의 실제 정의. 새 클래스를 쓰기 전에 grep 한다.
- `components/general/<Name>/<Name>.prompt.md` · `<Name>.d.ts`: 19개 컴포넌트의 props 와 조합 예.

## 한 화면의 뼈대

```jsx
const { Icon, Empty, ProgressBar } = window.SCAX;
<div className="page-surface">
  <div className="page-head">
    <div><h1>내 업무</h1><p>기한이 오늘까지인 업무 3건</p></div>
    <div className="page-head-actions">
      <button className="btn primary"><Icon name="plus" /> 새 업무</button>
    </div>
  </div>
  <div className="toolbar">
    <div className="toolbar-group">
      <button className="filter-chip on">진행 중 <Icon name="chevron-down" size={12} /></button>
      <button className="filter-chip">담당 <Icon name="chevron-down" size={12} /></button>
    </div>
  </div>
  <section className="surface-card">
    <div className="card-title"><h3>체크리스트</h3><span className="checklist-progress">2 / 5</span></div>
    <ProgressBar done={2} total={5} />
    <table className="plain-table">
      <thead><tr><th>업무</th><th className="center">상태</th><th className="end">종료일</th></tr></thead>
      <tbody><tr className="openable"><td className="title-cell">제품 소개서 내용 업데이트</td>
        <td className="center"><span className="status in_progress">진행 중</span></td><td className="end">09월 12일</td></tr></tbody>
    </table>
  </section>
</div>
```
