/* 「내 업무」 화면. 공용 틀은 ../../shell/js/scax-ui.jsx 가 먼저 싣는다. */
const {
  Icon, Button, ButtonGroup, IconButton, Badge, Popover, Select, SegmentedControl, Tabs, Chip,
  GutterList, Empty, StatusNote, Skeleton, AppShell, SideNav, AppHeader, AppBody, StateSwitch,
  TaskCreateModal,
} = window;

/* ===== mywork.jsx → README 「파일 → 이식 위치」 참고 ===== */
/* 「내 업무」 screen regions. Each region renders all four states: default / empty / loading / error.
   Copy marked 「상태 문구」 in README.md is the only copy not present on the current screen. */

function InboxRail({ state, items, filter, onFilter, onResolve }) {
  const shown = filter === 'all' ? items : items.filter((i) => i.kind === filter);
  const header = <SegmentedControl ariaLabel="수신함 분류" value={filter} options={INBOX_FILTERS} onChange={onFilter} />;
  let body;
  if (state === 'loading') {
    body = <div className="scax-skeleton-stack">{[0, 1, 2].map((i) => <Skeleton key={i} variant="card" />)}</div>;
  } else if (state === 'error') {
    body = <StatusNote title="수신함을 불러오지 못했습니다" desc="잠시 후 다시 시도해 주세요." action={<Button size="sm" variant="outlined" tone="neutral" label="다시 시도" />} />;
  } else if (state === 'empty' || !shown.length) {
    body = <Empty title="새로운 수신 항목이 없습니다" desc="업무·참고 알림이 도착하면 여기에 표시됩니다." />;
  } else {
    body = shown.map((it) => <InboxCard key={it.id} item={it} onResolve={() => onResolve(it.id)} />);
  }
  return (
    <GutterList title="수신함" count={state === 'default' ? INBOX_UNREAD_COUNT : undefined} headerEnd={header}>
      {body}
    </GutterList>
  );
}

/* InboxCard 「신규」 — the DS calls these InboxTask / InboxNotice. */
function InboxCard({ item, onResolve }) {
  return (
    <article className="scax-inbox-card">
      <div className="scax-inbox-card__content">
      <div className="scax-inbox-card__top">
        <Badge tone={item.kind === 'task' ? 'accent' : 'neutral'}>{item.badge}</Badge>
        {item.kind === 'task' ? <IconButton name="plus" size={20} label="업무로 추가" /> : null}
      </div>
      <h3 className="scax-inbox-card__title">{item.title}</h3>
      {item.excerpt ? <p className="scax-inbox-card__excerpt">{item.excerpt}</p> : null}
      <div className="scax-inbox-card__meta">
        <img className="scax-inbox-card__meta-avatar" src={item.avatar} alt="" />
        <span className="scax-inbox-card__meta-who">{item.who}</span>
        <span className="scax-inbox-card__meta-sep" />
        <span>{item.date}</span>
        <span className="scax-inbox-card__meta-sep" />
        <span className="scax-inbox-card__source">
          <Icon name={item.sourceIcon} size={16} />
          {item.source}
          <Icon name="external-link" size={12} />
        </span>
      </div>
      </div>
      <ButtonGroup>
        <Button variant="outlined" tone="neutral" label={item.primary} />
        <Button variant="outlined" tone={item.kind === 'task' ? 'primary' : 'neutral'} label={item.secondary} onClick={item.secondary === '확인완료' ? onResolve : undefined} />
      </ButtonGroup>
    </article>
  );
}

function TaskPanel({ state, tab, onTab, filter, onFilter, rows, onStatus, onStar, onResolveRow, onSentRequest }) {
  const filters = tab === 'sent' ? SENT_TASK_FILTERS : tab === 'done' ? DONE_TASK_FILTERS : TASK_FILTERS;
  return (
    <React.Fragment>
      <Tabs ariaLabel="업무 목록" value={tab} options={WORK_TABS} onChange={onTab} />
      <div className="scax-chip-bar">
        {filters.map((f) => <Chip key={f.value} label={f.label} on={f.value === filter} onClick={() => onFilter(f.value)} />)}
        <span className="scax-chip-bar__end">
          <Button variant="outlined" tone="neutral" size="sm" label="WBS 보기" />
        </span>
      </div>
      {tab === 'my' ? <TaskTable state={state} rows={rows} onStatus={onStatus} onStar={onStar} onResolveRow={onResolveRow} /> : null}
      {tab === 'sent' ? <SentTaskTable state={state} rows={SENT_ROWS} onRequest={onSentRequest} /> : null}
      {tab === 'done' ? <DoneTaskTable state={state} groups={DONE_GROUPS} /> : null}
    </React.Fragment>
  );
}

/* 테이블 상태(로딩/오류/비어 있음)는 세 테이블이 공유한다. */
function TableState({ state, cols, emptyIcon, emptyTitle, emptyDesc, errorTitle }) {
  if (state === 'loading') {
    return [0, 1, 2, 3, 4, 5].map((i) => (
      <div className="scax-skeleton-row" key={i}>
        {Array.from({ length: cols }).map((_, c) => <Skeleton key={c} variant={c === 1 ? 'title' : 'text'} />)}
      </div>
    ));
  }
  if (state === 'error') {
    return <StatusNote title={errorTitle} desc="네트워크 상태를 확인한 뒤 다시 시도해 주세요." action={<Button variant="outlined" tone="neutral" label="다시 시도" />} />;
  }
  if (state === 'empty') return <Empty icon={emptyIcon} title={emptyTitle} desc={emptyDesc} />;
  return null;
}

/* TaskTable 「신규」 — 내 업무 */
function TaskTable({ state, rows, onStatus, onStar, onResolveRow }) {
  return (
    <div className="scax-task-table">
      <div className="scax-task-table__head" role="row">
        <span />
        <span>제목/종류</span>
        <span className="scax-task-table__cell--center">요청자</span>
        <span className="scax-task-table__cell--center">기한</span>
        <span className="scax-task-table__cell--center">상태</span>
        <span className="scax-task-table__cell--center">액션</span>
      </div>
      <div className="scax-task-table__body">
        <TableState state={state} cols={6} emptyIcon="square-check" emptyTitle="표시할 업무가 없습니다" emptyDesc="새 업무를 만들거나 수신함에서 업무로 옮겨 보세요." errorTitle="업무 목록을 불러오지 못했습니다" />
        {state === 'default' ? rows.map((r) => (
          <div className="scax-task-table__row" role="row" key={r.id}>
            <span className="scax-task-table__cell--center">
              <IconButton name={r.starred ? 'star-fill' : 'star'} label="중요 표시" active={r.starred} onClick={() => onStar(r.id)} />
            </span>
            <span className="scax-task-table__cell--title">
              <span className={`scax-task-table__title${r.unread ? ' scax-task-table__title--unread' : ''}`}>{r.title}</span>
              {r.badge ? <Badge tone={r.badgeTone}>{r.badge}</Badge> : null}
            </span>
            <span className="scax-task-table__cell--center scax-task-table__cell--muted">{r.requester}</span>
            <span className="scax-task-table__cell--center">
              <span className="scax-task-table__due">
                {r.due}
                {r.dueExtra ? <span className="scax-task-table__due-extra">{r.dueExtra}</span> : null}
              </span>
            </span>
            <span className="scax-task-table__cell--center">
              <Select ariaLabel="상태" value={r.status} options={STATUS_OPTIONS} onChange={(v) => onStatus(r.id, v)} />
            </span>
            <span className="scax-task-table__cell--actions">
              {r.resolved ? <span className="scax-task-table__resolved">{r.resolved}</span> : null}
              {!r.resolved && r.actions === 'confirm' ? (
                <React.Fragment>
                  <Button size="sm" variant="outlined" tone="primary" label="확인" onClick={() => onResolveRow(r.id, '확인')} />
                  <Button size="sm" variant="outlined" tone="neutral" label="보완 요청" onClick={() => onResolveRow(r.id, '보완 요청')} />
                </React.Fragment>
              ) : null}
              {!r.resolved && r.actions === 'accept' ? (
                <React.Fragment>
                  <Button size="sm" variant="outlined" tone="primary" label="수락" onClick={() => onResolveRow(r.id, '수락')} />
                  <Button size="sm" variant="outlined" tone="neutral" label="거절" onClick={() => onResolveRow(r.id, '거절')} />
                </React.Fragment>
              ) : null}
            </span>
          </div>
        )) : null}
      </div>
    </div>
  );
}

/* SentTaskTable 「신규」 — 보낸 업무. 상태는 읽기 전용 배지, 액션은 「다시 요청」 하나. */
function SentTaskTable({ state, rows, onRequest }) {
  return (
    <div className="scax-task-table scax-task-table--sent">
      <div className="scax-task-table__head" role="row">
        <span />
        <span>제목</span>
        <span className="scax-task-table__cell--center">담당자</span>
        <span className="scax-task-table__cell--center">기한</span>
        <span className="scax-task-table__cell--center">상태</span>
        <span className="scax-task-table__cell--center">액션</span>
      </div>
      <div className="scax-task-table__body">
        <TableState state={state} cols={6} emptyIcon="send" emptyTitle="보낸 업무가 없습니다" emptyDesc="업무를 만들어 담당자에게 요청해 보세요." errorTitle="보낸 업무를 불러오지 못했습니다" />
        {state === 'default' ? rows.map((r) => (
          <div className="scax-task-table__row" role="row" key={r.id}>
            <span />
            <span className="scax-task-table__cell--title">
              <span className="scax-task-table__title">{r.title}</span>
            </span>
            <span className="scax-task-table__cell--center scax-task-table__cell--muted">{r.assignee}</span>
            <span className="scax-task-table__cell--center">
              <span className="scax-task-table__due">
                {r.due}
                {r.dueExtra ? <span className="scax-task-table__due-extra">{r.dueExtra}</span> : null}
              </span>
            </span>
            <span className="scax-task-table__cell--center">
              <Badge tone={r.status === 'progress' ? 'accent' : 'neutral'}>{SENT_STATUS_LABEL[r.status]}</Badge>
            </span>
            <span className="scax-task-table__cell--actions scax-task-table__cell--actions-single">
              <Button size="sm" variant="outlined" tone="neutral" label="다시 요청" onClick={() => onRequest(r.id)} />
            </span>
          </div>
        )) : null}
      </div>
    </div>
  );
}

/* DoneTaskTable 「신규」 — 완료 업무. 내 업무 / 보낸 업무로 그룹지어 보여준다. */
function DoneTaskTable({ state, groups }) {
  const [closed, setClosed] = React.useState({});
  return (
    <div className="scax-task-table scax-task-table--done">
      <div className="scax-task-table__head" role="row">
        <span />
        <span>제목</span>
        <span className="scax-task-table__cell--center">상대</span>
        <span className="scax-task-table__cell--center">처리일</span>
        <span className="scax-task-table__cell--center">상태</span>
        <span className="scax-task-table__cell--center">액션</span>
      </div>
      <div className="scax-task-table__body">
        <TableState state={state} cols={6} emptyIcon="circle-check" emptyTitle="완료된 업무가 없습니다" emptyDesc="처리가 끝난 업무가 여기에 모입니다." errorTitle="완료 업무를 불러오지 못했습니다" />
        {state === 'default' ? groups.map((g) => (
          <React.Fragment key={g.id}>
            <button type="button" className="scax-task-table__group" aria-expanded={!closed[g.id]} onClick={() => setClosed((c) => Object.assign({}, c, { [g.id]: !c[g.id] }))}>
              <Icon name={closed[g.id] ? 'chevron-right-small' : 'caret-down'} size={16} />
              {g.label}
            </button>
            {closed[g.id] ? null : g.rows.map((r) => (
              <div className="scax-task-table__row" role="row" key={r.id}>
                <span />
                <span className="scax-task-table__cell--title scax-task-table__cell--indent">
                  <span className="scax-task-table__title">{r.title}</span>
                </span>
                <span className="scax-task-table__cell--center scax-task-table__cell--muted">{r.counterpart}</span>
                <span className="scax-task-table__cell--center scax-task-table__cell--muted">{r.closedAt}</span>
                <span className="scax-task-table__cell--center">
                  <Badge tone={DONE_STATUS[r.status].tone}>{DONE_STATUS[r.status].label}</Badge>
                </span>
                <span />
              </div>
            ))}
          </React.Fragment>
        )) : null}
      </div>
    </div>
  );
}

function CalendarRail({ state, range, onRange, items }) {
  let body;
  if (state === 'loading') {
    body = <div className="scax-skeleton-stack">{[0, 1, 2].map((i) => <Skeleton key={i} variant="agenda" />)}</div>;
  } else if (state === 'error') {
    body = <StatusNote title="일정을 불러오지 못했습니다" desc="잠시 후 다시 시도해 주세요." action={<Button size="sm" variant="outlined" tone="neutral" label="다시 시도" />} />;
  } else if (state === 'empty') {
    body = <Empty icon="calendar" title="등록된 일정이 없습니다" />;
  } else if (range === 'week') {
    body = <WeekView />;
  } else if (range === 'month') {
    body = <MonthView items={items} />;
  } else {
    body = (
      <React.Fragment>
        <div className="scax-calendar-rail__day">
          <p className="scax-calendar-rail__date">{WORK_DAY.date}</p>
          <p className="scax-calendar-rail__hours">
            <span className="scax-calendar-rail__hours-label"><Icon name="clock" size={16} />근무 시간</span>
            <span className="scax-calendar-rail__hours-value">{WORK_DAY.hours}</span>
          </p>
        </div>
        <div className="scax-calendar-rail__list">{items.map((e) => <AgendaItem key={e.id} item={e} />)}</div>
      </React.Fragment>
    );
  }
  return (
    <section className="scax-calendar-rail">
      <header className="scax-calendar-rail__header">
        <h2 className="scax-calendar-rail__title">캘린더</h2>
        <SegmentedControl ariaLabel="캘린더 범위" value={range} options={CALENDAR_RANGES} onChange={onRange} />
      </header>
      {body}
    </section>
  );
}

function AgendaItem({ item }) {
  return (
    <article className="scax-agenda-item">
      <div className="scax-agenda-item__top">
        <Badge tone={item.tone}>{item.badge}</Badge>
        <span className="scax-agenda-item__time">{item.time}</span>
      </div>
      <p className="scax-agenda-item__title">{item.title}</p>
      {item.sub ? <p className="scax-agenda-item__sub">{item.sub}</p> : null}
    </article>
  );
}

/* CalendarNav 「신규」 — 기간 라벨 + 이전/다음. 주·월 뷰가 공유한다. */
function CalendarNav({ label }) {
  return (
    <div className="scax-calendar-nav">
      <span className="scax-calendar-nav__label">{label}</span>
      <IconButton name="chevron-left-small" size={20} label="이전" />
      <IconButton name="chevron-right-small" size={20} label="다음" />
    </div>
  );
}

function DayCell({ day, onSelect }) {
  if (!day) return <span className="scax-day-cell scax-day-cell--blank" />;
  const cls = ['scax-day-cell'];
  if (day.selected) cls.push('scax-day-cell--selected');
  if (day.muted) cls.push('scax-day-cell--muted');
  return (
    <button type="button" className={cls.join(' ')} aria-pressed={!!day.selected} onClick={onSelect}>
      {day.date}
      {day.dot ? <span className="scax-day-cell__dot" /> : null}
    </button>
  );
}

/* WeekView 「신규」 — 요일 스트립 + 날짜별 접이식 일정. */
function WeekView() {
  const [open, setOpen] = React.useState(() => {
    const init = {};
    WEEK_SECTIONS.forEach((s) => { if (s.open) init[s.id] = true; });
    return init;
  });
  return (
    <React.Fragment>
      <CalendarNav label={WEEK_LABEL} />
      <div className="scax-day-strip">
        {WEEKDAY_HEADS.map((h) => <span className="scax-day-strip__head" key={h}>{h}</span>)}
        {WEEK_DAYS.map((d) => <DayCell key={d.date} day={d} />)}
      </div>
      <div className="scax-calendar-rail__list">
        {WEEK_SECTIONS.map((s) => (
          <section className="scax-day-section" key={s.id}>
            <button type="button" className="scax-day-section__head" aria-expanded={!!open[s.id]} onClick={() => setOpen((o) => Object.assign({}, o, { [s.id]: !o[s.id] }))}>
              <span className="scax-day-section__label">{s.label} ({s.count})</span>
              <Icon name={open[s.id] ? 'caret-up' : 'caret-down'} size={16} />
            </button>
            {open[s.id] && s.items.length ? (
              <div className="scax-day-section__body">{s.items.map((e) => <AgendaItem key={e.id} item={e} />)}</div>
            ) : null}
          </section>
        ))}
      </div>
    </React.Fragment>
  );
}

/* MonthView 「신규」 — 월 그리드 + 선택일 일정. */
function MonthView({ items }) {
  return (
    <React.Fragment>
      <CalendarNav label={MONTH_LABEL} />
      <div className="scax-month-grid">
        {WEEKDAY_HEADS.map((h) => <span className="scax-month-grid__head" key={h}>{h}</span>)}
        {MONTH_WEEKS.map((week, wi) => week.map((day, di) => <DayCell key={wi + '-' + di} day={day} />))}
      </div>
      <div className="scax-calendar-rail__list">{items.map((e) => <AgendaItem key={e.id} item={e} />)}</div>
    </React.Fragment>
  );
}

/* AgentBubble 「신규」 */
function AgentBubble({ message }) {
  return (
    <div className="scax-agent">
      <p className="scax-agent__bubble">{message}</p>
      <div className="scax-agent__orb">
        <span className="scax-agent__glow" />
        <img className="scax-agent__img" src="./assets/ai-agent-orb.png" alt="AI 에이전트" />
      </div>
    </div>
  );
}

/* ===== app.jsx → README 「파일 → 이식 위치」 참고 ===== */
/* 「내 업무」 page — composition + screen state. */

function MyWorkPage() {
  const [screenState, setScreenState] = React.useState('default');
  const [navId, setNavId] = React.useState('work');
  const [navCollapsed, setNavCollapsed] = React.useState(false);
  const [createOpen, setCreateOpen] = React.useState(false);
  const [toast, setToast] = React.useState('');
  const [inboxFilter, setInboxFilter] = React.useState('all');
  const [inboxItems, setInboxItems] = React.useState(INBOX_ITEMS);
  const [tab, setTab] = React.useState('my');
  const [taskFilter, setTaskFilter] = React.useState('all');
  const changeTab = (t) => { setTab(t); setTaskFilter('all'); };
  const [rows, setRows] = React.useState(TASK_ROWS);
  const [range, setRange] = React.useState('today');

  const resolveInbox = (id) => setInboxItems((list) => list.filter((i) => i.id !== id));
  const setStatus = (id, status) => setRows((list) => list.map((r) => (r.id === id ? Object.assign({}, r, { status }) : r)));
  const toggleStar = (id) => setRows((list) => list.map((r) => (r.id === id ? Object.assign({}, r, { starred: !r.starred }) : r)));
  const resolveRow = (id, label) => setRows((list) => list.map((r) => (r.id === id ? Object.assign({}, r, { resolved: label, unread: false }) : r)));

  return (
    <React.Fragment>
      <AppShell nav={<SideNav user={{ name: '유하람님', role: '기획자', avatar: './assets/avatar-person.png' }} activeId={navId} onSelect={setNavId} collapsed={navCollapsed} onCollapse={() => setNavCollapsed((v) => !v)} />}>
        <AppHeader
          title="업무"
          actions={(
            <React.Fragment>
              <Button variant="outlined" tone="primary" label="일일보고 생성" />
              <Button variant="solid" tone="primary" label="업무 만들기" onClick={() => setCreateOpen(true)} />
            </React.Fragment>
          )}
        />
        <AppBody
          railLeft={<InboxRail state={screenState} items={inboxItems} filter={inboxFilter} onFilter={setInboxFilter} onResolve={resolveInbox} />}
          railRight={<CalendarRail state={screenState} range={range} onRange={setRange} items={AGENDA_ITEMS} />}
        >
          <TaskPanel
            state={screenState}
            tab={tab}
            onTab={changeTab}
            filter={taskFilter}
            onFilter={setTaskFilter}
            rows={rows}
            onStatus={setStatus}
            onStar={toggleStar}
            onResolveRow={resolveRow}
            onSentRequest={() => {}}
          />
        </AppBody>
      </AppShell>
      <TaskCreateModal open={createOpen} onClose={() => setCreateOpen(false)} onToast={setToast} />
      <Toast message={toast} onDone={() => setToast('')} />
      <AgentBubble message={AGENT_MESSAGE} />
      <StateSwitch value={screenState} onChange={setScreenState} />
    </React.Fragment>
  );
}

/* 단일 엔트리라 로딩 순서 문제가 없다 — 루트 하나를 만들고 바로 마운트한다. */
const scaxRoot = ReactDOM.createRoot(document.getElementById('root'));
scaxRoot.render(<MyWorkPage />);
