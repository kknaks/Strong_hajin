/* 업무 만들기 모달 (MOD-101) — 업무 탭과 회의 탭이 같은 자리를 쓴다.
   회의의 「업무 생성」도 이 모달을 열고, 후속업무 후보 값만 draft 로 넘긴다.
   공용 틀(scax-ui.jsx)이 먼저 실려 있어야 한다. */
const { Icon, Button, IconButton } = window;

/* 화면 데이터가 없을 때 쓰는 기본 표본 */
const TASK_MODAL_DRAFT = { name: '', due: '', body: '', bodyLimit: 200 };
const TASK_MODAL_PEOPLE = ['홍길동', '박민수', '유하람'];
const TASK_MODAL_FILES = [{ id: 'f1', icon: 'document', name: '계약서_초안.pdf', size: '340KB' }];

/* ===== 업무 만들기 모달 (MOD-101) ===== */

/* Modal — 헤더 고정 / 본문 max-height + 스크롤 / 푸터 고정. 본문이 짧으면 콘텐츠를 감싼 높이. */
function Modal({ open, title, onClose, footer, children }) {
  React.useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="scax-modal-overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="scax-modal" role="dialog" aria-modal="true" aria-label={title}>
        <header className="scax-modal__head">
          <h2 className="scax-modal__title">{title}</h2>
          <IconButton name="close" size={24} label="닫기" onClick={onClose} />
        </header>
        <div className="scax-modal__body scax-scroll">{children}</div>
        <footer className="scax-modal__foot">{footer}</footer>
      </div>
    </div>
  );
}

function Field({ label, required, hint, grow, children }) {
  return (
    <div className={`scax-field${grow ? ' scax-field--grow' : ''}`}>
      {label ? (
        <label className="scax-field__label">
          {label}
          {required ? <span className="scax-field__required">*</span> : null}
        </label>
      ) : null}
      {children}
      {hint ? <p className="scax-field__hint">{hint}</p> : null}
    </div>
  );
}

function TextField({ value, onChange, placeholder, clearable, ariaLabel }) {
  return (
    <div className="scax-textfield">
      <input className="scax-textfield__input" value={value} placeholder={placeholder} aria-label={ariaLabel} onChange={(e) => onChange(e.target.value)} />
      {clearable && value ? <IconButton name="circle-close" size={20} label="지우기" className="scax-textfield__clear" onClick={() => onChange('')} /> : null}
    </div>
  );
}

/* DateField — 네이티브 date 입력 + 캘린더 어포던스. DatePicker 는 브라우저 기본을 쓴다. */
function DateField({ value, onChange, ariaLabel }) {
  const ref = React.useRef(null);
  return (
    <div className="scax-textfield">
      <input ref={ref} type="date" className="scax-textfield__input scax-textfield__input--date" value={value} aria-label={ariaLabel} onChange={(e) => onChange(e.target.value)} />
      <IconButton name="calendar" size={20} label="날짜 선택" className="scax-textfield__clear" onClick={() => { if (ref.current.showPicker) ref.current.showPicker(); }} />
    </div>
  );
}

/* Composer — 여러 줄 입력 + 글자 수. max-height 를 넘기면 본문이 스크롤된다. */
function Composer({ value, onChange, limit, ariaLabel }) {
  const full = value.length >= limit;
  return (
    <div className={`scax-composer${full ? ' scax-composer--full' : ''}`}>
      <textarea className="scax-composer__input scax-scroll" value={value} maxLength={limit} aria-label={ariaLabel} onChange={(e) => onChange(e.target.value)} />
      <span className="scax-composer__count">{value.length}/{limit}</span>
    </div>
  );
}

/* AutoComplete — 이름 검색 콤보박스. 추천 배지는 이번 스펙 제외. */
function AutoComplete({ value, onChange, options, placeholder }) {
  const [open, setOpen] = React.useState(false);
  const matches = value ? options.filter((o) => o.startsWith(value.slice(0, 1))) : options;
  return (
    <div className="scax-autocomplete">
      <div className="scax-textfield scax-textfield--search">
        <input
          className="scax-textfield__input"
          value={value}
          placeholder={placeholder}
          aria-label="확인받을 사람 검색"
          role="combobox"
          aria-expanded={open}
          onChange={(e) => { onChange(e.target.value); setOpen(true); }}
          onFocus={() => setOpen(!!value)}
          onBlur={() => window.setTimeout(() => setOpen(false), 120)}
        />
        {value ? <IconButton name="circle-close" size={20} label="지우기" onClick={() => onChange('')} /> : null}
        <Icon name="search" size={20} />
      </div>
      {open && matches.length ? (
        <ul className="scax-autocomplete__list" role="listbox">
          {matches.map((o, i) => (
            <li key={o + i}>
              <button type="button" role="option" aria-selected={o === value} className={`scax-autocomplete__option${o === value ? ' scax-autocomplete__option--selected' : ''}`} onMouseDown={() => { onChange(o); setOpen(false); }}>
                {o}
                {o === value ? <Icon name="check" size={16} /> : null}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/* Checklist 「신규」 — 항목 추가·체크·삭제. 업무 만들기 모달 우열에 들어간다. */
function Checklist({ items, onToggle, onText, onAdd, onRemove }) {
  return (
    <div className="scax-checklist">
      <ul className="scax-checklist__list scax-scroll">
        {items.map((it) => (
          <li className="scax-checklist__row" key={it.id}>
            <label className="scax-checkbox">
              <input type="checkbox" className="scax-checkbox__input" checked={it.done} onChange={() => onToggle(it.id)} />
              <span className="scax-checkbox__box"><Icon name="check" size={14} /></span>
            </label>
            <input
              className="scax-checklist__text"
              value={it.text}
              placeholder="할 일을 입력하세요"
              aria-label="체크리스트 항목"
              onChange={(e) => onText(it.id, e.target.value)}
            />
            <IconButton name="close" size={20} label="항목 삭제" onClick={() => onRemove(it.id)} />
          </li>
        ))}
      </ul>
      <Button variant="outlined" tone="neutral" size="sm" iconBefore="plus" label="항목 추가" onClick={onAdd} />
    </div>
  );
}

/* DropZone + FileList — 드래그 상태와 목록. 삭제 알림은 전역 Toast 가 맡는다. */
function DropZone({ files, onAdd, onRemove }) {
  const [over, setOver] = React.useState(false);
  return (
    <div
      className={`scax-dropzone${over ? ' scax-dropzone--over' : ''}`}
      onDragOver={(e) => { e.preventDefault(); setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); onAdd(); }}
    >
      <div className="scax-dropzone__head">
        <span className="scax-dropzone__hint">첨부할 파일을 끌어다 놓거나 추가하세요</span>
        <Button size="sm" variant="solid" tone="primary" label="파일 추가" onClick={onAdd} />
      </div>
      {over ? <span className="scax-dropzone__cursor"><Icon name="document" size={20} /></span> : null}
      {files.length ? (
        <ul className="scax-file-list">
          {files.map((f) => (
            <li className="scax-file-row" key={f.id}>
              <Icon name={f.icon} size={20} />
              <span className="scax-file-row__name">{f.name}</span>
              {f.size ? <span className="scax-file-row__size">{f.size}</span> : null}
              <IconButton name="close" size={20} label="첨부 삭제" onClick={() => onRemove(f.id)} />
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/* Toast 「신규」 — 화면 하단 고정 레이어. 짧은 결과 알림만 담는다. */
function Toast({ message, onDone }) {
  React.useEffect(() => {
    if (!message) return undefined;
    const t = window.setTimeout(onDone, 3000);
    return () => window.clearTimeout(t);
  }, [message, onDone]);
  if (!message) return null;
  return (
    <div className="scax-toast" role="status" aria-live="polite">
      <Icon name="trash" size={20} />
      <span className="scax-toast__text">{message}</span>
    </div>
  );
}

/* TaskCreateModal 「신규」 — 진입 → 입력 → 추가 입력(확인받을 사람 · 파일 첨부) 순으로 열린다. */
function TaskCreateModal({ open, onClose, onToast, draft }) {
  const d = Object.assign({}, TASK_MODAL_DRAFT, window.TASK_DRAFT || null, draft || null);
  const [name, setName] = React.useState(d.name);
  const [due, setDue] = React.useState(d.due);
  const [body, setBody] = React.useState(d.body);
  const [person, setPerson] = React.useState('');
  const [files, setFiles] = React.useState([]);
  const [checks, setChecks] = React.useState([{ id: 'c1', text: '계약 조항 확인', done: false }, { id: 'c2', text: '수정 의견 회신', done: false }]);
  const addFiles = () => setFiles(window.SAMPLE_FILES || TASK_MODAL_FILES);
  const removeFile = (id) => {
    setFiles((list) => list.filter((x) => x.id !== id));
    onToast('파일이 삭제되었습니다.');
  };
  return (
    <Modal
      open={open}
      title="업무 만들기"
      onClose={onClose}
      footer={(
        <React.Fragment>
          <Button variant="outlined" tone="neutral" label="취소" onClick={onClose} />
          <Button variant="solid" tone="primary" label="생성" disabled={!name} onClick={onClose} />
        </React.Fragment>
      )}
    >
      <div className="scax-modal-grid">
        <span className="scax-modal-grid__divider" aria-hidden="true" />
        <Field label="업무 명" required>
          <TextField value={name} onChange={setName} placeholder="업무 명을 입력하세요" clearable ariaLabel="업무 명" />
        </Field>
        <Field label="기한">
          <DateField value={due} onChange={setDue} ariaLabel="기한" />
        </Field>
        <Field label="내용" hint={body.length >= d.bodyLimit ? '200자 까지 입력 가능합니다' : undefined}>
          <Composer value={body} onChange={setBody} limit={d.bodyLimit} ariaLabel="내용" />
        </Field>
        <Field label="확인받을 사람 (선택)">
          <AutoComplete value={person} onChange={setPerson} options={window.PEOPLE_OPTIONS || TASK_MODAL_PEOPLE} placeholder="이름으로 찾기" />
        </Field>
        <Field label="체크리스트">
          <Checklist
            items={checks}
            onToggle={(id) => setChecks((l) => l.map((x) => (x.id === id ? Object.assign({}, x, { done: !x.done }) : x)))}
            onText={(id, text) => setChecks((l) => l.map((x) => (x.id === id ? Object.assign({}, x, { text }) : x)))}
            onAdd={() => setChecks((l) => l.concat([{ id: 'c' + (l.length + 1) + Date.now(), text: '', done: false }]))}
            onRemove={(id) => setChecks((l) => l.filter((x) => x.id !== id))}
          />
        </Field>
        <Field label="첨부파일">
          <DropZone files={files} onAdd={addFiles} onRemove={removeFile} />
        </Field>
      </div>
    </Modal>
  );
}


Object.assign(window, { Modal, Field, TextField, DateField, Composer, AutoComplete, Checklist, DropZone, Toast, TaskCreateModal });
