/* SCAX 공용 틀 — 모든 화면이 이 파일을 먼저 읽는다.
   부품(primitives) + 페이지 틀(AppShell · SideNav · AppHeader · AppBody) + dev 상태 토글.
   화면 파일은 여기 정의된 이름을 그대로 쓴다 (Babel 은 파일별 스코프라 window 로 넘긴다). */

/* 「내 업무」 단일 엔트리.
   브라우저에서 Babel 이 파일별 스코프로 변환하기 때문에 파일을 쪼개면 로딩 순서가 보장되지 않는다.
   그래서 프리뷰는 이 한 파일로 로드하고, 실제 이식 시에는 아래 구분 주석대로 파일을 나눈다. */

/* ===== primitives.jsx → README 「파일 → 이식 위치」 참고 ===== */
/* SCAX primitives — our stack's part names. React function components, CSS classes only.
   Preview runtime is React 18 UMD; the source is plain React 19-compatible JSX (no hooks removed,
   no legacy APIs). Move each export into src/components/<Name>.jsx as-is.
   NOTE Icon: in this preview the glyph set is read from the SCAX DS bundle (window.SCAX_DS.Icon).
   In the app, Icon keeps its current signature <Icon name size /> and reads our own sprite. */

const { useState, useRef, useEffect, useCallback } = React;

/* Icon — DS 아이콘(선 글리프). 패스는 DS `components/icon` 의 Icon/Normal 세트. */
function Icon({ name, size = 20, className }) {
  const DSIcon = (window.SCAX_DS || {}).Icon;
  if (!DSIcon) return null;
  return <DSIcon name={name} size={size} className={className} />;
}

function Button({ variant = 'outlined', tone = 'neutral', size = 'md', label, iconBefore, block, disabled, onClick, type = 'button' }) {
  const cls = ['scax-button', `scax-button--${variant}-${tone}`];
  if (size !== 'md') cls.push(`scax-button--${size}`);
  if (block) cls.push('scax-button--block');
  return (
    <button type={type} className={cls.join(' ')} disabled={disabled} onClick={onClick}>
      {iconBefore ? <Icon name={iconBefore} size={16} /> : null}
      {label}
    </button>
  );
}

function ButtonGroup({ children }) {
  return <div className="scax-button-group">{children}</div>;
}

function IconButton({ name, size = 20, label, onClick, active, className }) {
  const cls = ['scax-icon-button'];
  if (active) cls.push('scax-icon-button--star-on');
  if (className) cls.push(className);
  return (
    <button type="button" className={cls.join(' ')} aria-label={label} aria-pressed={active} onClick={onClick}>
      <Icon name={name} size={size} />
    </button>
  );
}

function Badge({ tone = 'neutral', variant, children }) {
  const cls = ['scax-badge', `scax-badge--${variant === 'count' ? 'count' : tone}`];
  if (variant === 'count') cls[1] = 'scax-badge--count';
  return <span className={cls.join(' ')}>{children}</span>;
}

/* Popover — floating layer; closes on outside click and Escape. */
function Popover({ open, onClose, placement = 'below-start', children }) {
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => { if (ref.current && !ref.current.parentNode.contains(e.target)) onClose(); };
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('mousedown', onDown); document.removeEventListener('keydown', onKey); };
  }, [open, onClose]);
  if (!open) return null;
  return <div ref={ref} className={`scax-popover scax-popover--${placement}`} role="listbox">{children}</div>;
}

/* Select — trigger + Popover options. Used for 상태. */
function Select({ value, options, tone = 'accent', onChange, ariaLabel }) {
  const [open, setOpen] = useState(false);
  const close = useCallback(() => setOpen(false), []);
  const current = options.find((o) => o.value === value) || options[0];
  const triggerTone = current.tone || tone;
  return (
    <div className="scax-select">
      <button
        type="button"
        className={`scax-select__trigger${triggerTone === 'accent' ? '' : ` scax-select__trigger--${triggerTone}`}`}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={ariaLabel}
        onClick={() => setOpen((v) => !v)}
      >
        {current.label}
        <span className="scax-select__caret" aria-hidden="true" />
      </button>
      <Popover open={open} onClose={close}>
        {options.map((o) => (
          <button
            key={o.value}
            type="button"
            role="option"
            aria-selected={o.value === value}
            className={`scax-popover__option${o.value === value ? ' scax-popover__option--selected' : ''}`}
            onClick={() => { onChange(o.value); close(); }}
          >
            {o.label}
          </button>
        ))}
      </Popover>
    </div>
  );
}

/* SegmentedControl 「신규」 — the DS calls this SegmentedControl/Solid. */
function SegmentedControl({ value, options, onChange, ariaLabel }) {
  return (
    <div className="scax-segmented" role="tablist" aria-label={ariaLabel}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="tab"
          aria-selected={o.value === value}
          className={`scax-segmented__item${o.value === value ? ' scax-segmented__item--on' : ''}`}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/* Tabs 「신규」 */
function Tabs({ value, options, onChange, ariaLabel }) {
  return (
    <div className="scax-tabs" role="tablist" aria-label={ariaLabel}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="tab"
          aria-selected={o.value === value}
          className={`scax-tabs__item${o.value === value ? ' scax-tabs__item--on' : ''}`}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/* Chip 「신규」 — the DS calls this Category/Chip. */
function Chip({ label, on, onClick }) {
  return (
    <button type="button" className={`scax-chip${on ? ' scax-chip--on' : ''}`} aria-pressed={on} onClick={onClick}>
      {label}
    </button>
  );
}

/* GutterList — rail list with a header row and a scrolling body. */
function GutterList({ title, count, headerEnd, children }) {
  return (
    <section className="scax-gutter-list">
      <header className="scax-gutter-list__header">
        <h2 className="scax-gutter-list__title">
          <Icon name="inbox" size={24} />
          {title}
          {typeof count === 'number' ? <Badge variant="count">{count}</Badge> : null}
        </h2>
        {headerEnd}
      </header>
      <div className="scax-gutter-list__body">{children}</div>
    </section>
  );
}

function Empty({ icon = 'inbox', title, desc }) {
  return (
    <div className="scax-empty">
      <div className="scax-empty__icon"><Icon name={icon} size={24} /></div>
      <p className="scax-empty__title">{title}</p>
      {desc ? <p className="scax-empty__desc">{desc}</p> : null}
    </div>
  );
}

function StatusNote({ tone = 'danger', title, desc, action, inline }) {
  return (
    <div className={`scax-status-note${inline ? ' scax-status-note--inline' : ''}`} role="alert" data-tone={tone}>
      <div className="scax-status-note__icon"><Icon name="circle-exclamation" size={24} /></div>
      <p className="scax-status-note__title">{title}</p>
      {desc ? <p className="scax-status-note__desc">{desc}</p> : null}
      {action}
    </div>
  );
}

function Skeleton({ variant = 'text', width }) {
  return <div className={`scax-skeleton scax-skeleton--${variant}${width ? ` scax-skeleton--w-${width}` : ''}`} aria-hidden="true" />;
}

/* ===== 페이지 틀 ===== */
/* 틀 = AppShell · SideNav · AppHeader · AppBody.
   현재 DS 번들(_ds_bundle.js)에는 shell 부품이 없다 — 규칙(.scax-app-shell 외)은
   _ds_bundle.css 에 그대로 있으므로 여기서 그 규칙대로 조립한다.
   다음 design-sync 때 components/shell/ 이 올라오면 이 블록을 지우고 DS 것을 쓴다. */

function AppShell({ nav, children }) {
  return (
    <div className="scax-app-shell">
      {nav}
      <div className="scax-app-main">{children}</div>
    </div>
  );
}

/* 내비 목록 정본(nav.js)을 그대로 쓴다. 화면은 user·activeId 만 넘긴다. */
function SideNav({ user, activeId, onSelect, collapsed, onCollapse }) {
  const item = (it) => {
    const active = it.id === activeId;
    const cls = `scax-nav-item${active ? ' scax-nav-item--active' : ''}`;
    const inner = (
      <React.Fragment>
        <span className="scax-nav-item__glyph">
          <Icon name={it.icon} size={20} />
          {it.dot ? <span className="scax-nav-item__dot" /> : null}
        </span>
        {collapsed ? null : <span className="scax-nav-item__label">{it.label}</span>}
      </React.Fragment>
    );
    const shared = { key: it.id, className: cls, title: collapsed ? it.label : undefined, 'aria-current': active ? 'page' : undefined };
    if (it.href && !active) return <a {...shared} href={it.href}>{inner}</a>;
    return (
      <button {...shared} type="button" aria-disabled={it.href || onSelect ? undefined : 'true'} onClick={() => onSelect && onSelect(it.id)}>
        {inner}
      </button>
    );
  };
  return (
    <nav className={`scax-side-nav${collapsed ? ' scax-side-nav--collapsed' : ''}`} aria-label="주 메뉴">
      <div className="scax-side-nav__identity">
        <img className="scax-side-nav__avatar" src={user.avatar} alt="" />
        {collapsed ? null : (
          <span className="scax-side-nav__who">
            <span className="scax-side-nav__name">{user.name}</span>
            <span className="scax-side-nav__role">{user.role}</span>
          </span>
        )}
        <button type="button" className="scax-side-nav__collapse" aria-label={collapsed ? '메뉴 펴기' : '메뉴 접기'} aria-expanded={!collapsed} onClick={onCollapse}>
          <Icon name="left-side" size={20} />
        </button>
      </div>
      <div className="scax-side-nav__group">{NAV_PRIMARY.map(item)}</div>
      <div className="scax-side-nav__divider" />
      <div className="scax-side-nav__group">{NAV_MAIN.map(item)}</div>
      <div className="scax-side-nav__spacer" />
      <div className="scax-side-nav__footer">
        <span className="scax-side-nav__logo">{collapsed ? 'L' : 'LOGO'}</span>
        {collapsed ? null : <span className="scax-side-nav__version">v1.0</span>}
      </div>
    </nav>
  );
}

function AppHeader({ breadcrumb, title, actions }) {
  return (
    <header className="scax-page-header">
      <div className="scax-page-header__lead">
        {breadcrumb && breadcrumb.length ? (
          <nav className="scax-breadcrumb" aria-label="위치">
            {breadcrumb.map((b, i) => [
              i > 0 ? <span key={`s${i}`} className="scax-breadcrumb__sep">/</span> : null,
              <span key={b} className={`scax-breadcrumb__item${i === breadcrumb.length - 1 ? ' scax-breadcrumb__item--current' : ''}`}>{b}</span>,
            ])}
          </nav>
        ) : null}
        <h1 className="scax-page-header__title">{title}</h1>
      </div>
      <div className="scax-page-header__actions">{actions}</div>
    </header>
  );
}

function AppBody({ railLeft, railRight, children }) {
  return (
    <div className="scax-page-body">
      {railLeft ? <aside className="scax-page-body__rail scax-page-body__rail--left">{railLeft}</aside> : null}
      <main className="scax-page-body__content">{children}</main>
      {railRight ? <aside className="scax-page-body__rail scax-page-body__rail--right">{railRight}</aside> : null}
    </div>
  );
}

/* Dev-only. Not part of the product UI — remove when implementing. */
function StateSwitch({ value, onChange }) {
  const states = [
    { value: 'default', label: '기본' },
    { value: 'empty', label: '비어 있음' },
    { value: 'loading', label: '로딩' },
    { value: 'error', label: '오류' },
  ];
  return (
    <div className="scax-state-switch">
      <span className="scax-state-switch__label">상태</span>
      {states.map((s) => (
        <button key={s.value} type="button" className={`scax-state-switch__btn${s.value === value ? ' scax-state-switch__btn--on' : ''}`} onClick={() => onChange(s.value)}>
          {s.label}
        </button>
      ))}
    </div>
  );
}


/* AgentBubble 「신규」 — 우하단 고정. 업무·회의가 같은 자리에 같은 폼으로 쓴다 */
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

Object.assign(window, {
  Icon, Button, ButtonGroup, IconButton, Badge, Popover, Select, SegmentedControl, Tabs, Chip,
  GutterList, Empty, StatusNote, Skeleton, AppShell, SideNav, AppHeader, AppBody, StateSwitch, AgentBubble,
});
