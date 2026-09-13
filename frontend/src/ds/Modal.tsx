import { useEffect, useRef, type ReactNode } from "react";
import { Button, IconButton } from "./Button";
import { Icon } from "./icons/Icon";

/**
 * 지금 열려 있는 오버레이들 — 나중에 연 것이 뒤에 온다.
 *
 * **Esc 는 가장 위 한 겹만 닫는다.** 이 규칙이 없을 때는 Drawer 안에서 연 팝오버(`Select`)를 Esc 로
 * 닫으면 Drawer 까지 함께 닫혔다 — 팝오버는 document 에서, Drawer 는 window 에서 같은 Escape 를 각자
 * 받아 둘 다 자기 것으로 처리했기 때문이다. 쓰던 사유가 그대로 사라졌다.
 *
 * 그래서 닫히는 겹은 모두 이 hook 으로 등록하고, 맨 위가 아니면 그 Esc 는 자기 것이 아니다. 겹이 하나뿐인
 * 흔한 경우의 동작은 예전과 같다.
 */
const escapeLayers: symbol[] = [];

/** 한 겹을 Esc 스택에 올린다. `active` 가 거짓인 동안은 올리지 않는다 (닫혀 있는 팝오버). */
export function useEscape(onClose: () => void, active = true) {
  // 콜백이 매 렌더 새로 와도 겹의 순서는 그대로여야 한다 — 다시 등록하면 남의 위로 올라가 버린다.
  const latest = useRef(onClose);
  useEffect(() => {
    latest.current = onClose;
  });
  useEffect(() => {
    if (!active) return;
    const layer = Symbol("overlay");
    escapeLayers.push(layer);
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (escapeLayers[escapeLayers.length - 1] !== layer) return;
      latest.current();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      const at = escapeLayers.lastIndexOf(layer);
      if (at >= 0) escapeLayers.splice(at, 1);
    };
  }, [active]);
}

/**
 * Drawer — 뒤 화면을 남겨 둔 채 오른쪽에서 열리는 편집·상세 자리.
 *
 * 바퀴 3b 에서 새 DS 골격으로 갔다 — `workspace.css` 의 `.scax-drawer-overlay` / `.scax-drawer` /
 * `__head` / `__title` / `__body` / `__foot`. **껍데기는 DS 규약, 안의 구성은 우리 것**이다(사다리 ②).
 *
 * **폭은 사이즈 variant 로 연다 (바퀴 3b F-1 · `DS-gaps` G-10).** DS 골격은 520 하나뿐인데
 * 업무 상세 드로어는 폼이 두 열이라 520 으로는 좁다. `--sm`(520) / `--lg`(840) 두 단을 두고
 * **기본값을 `lg` 로 뒀다** — 지금 840 을 쓰던 6곳이 화면 그대로 남는다(E-1 과 같은 뜻).
 * 두 modifier 규칙은 `styles/overrides-transitional.css` 에 있다. DS 가 G-10 을 받으면 그리로 옮긴다.
 *
 * props 는 우리 것 그대로다(F-3). `size` 하나만 더했다.
 */
export function Drawer({
  label,
  kicker,
  title,
  headerExtra,
  footer,
  onClose,
  size = "lg",
  closeLabel,
  children,
}: {
  label: string;
  kicker?: string;
  title: string;
  headerExtra?: ReactNode;
  footer?: ReactNode;
  onClose: () => void;
  /** 골격 폭. `lg`(840) 가 기본 — 지금 열리는 여섯 자리가 전부 두 열 폼이다. */
  size?: "sm" | "lg";
  /** 닫기 단추의 이름 — 부르는 쪽이 준다 (바퀴 11: 부품은 말을 모른다) */
  closeLabel: string;
  children: ReactNode;
}) {
  useEscape(onClose);
  return (
    <div
      className="scax-drawer-overlay"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section aria-label={label} aria-modal="true" className={`scax-drawer scax-drawer--${size}`} role="dialog">
        <header className="scax-drawer__head">
          <div style={{ minWidth: 0 }}>
            {kicker && <small className="modal-kicker">{kicker}</small>}
            <h3 className="scax-drawer__title">{title}</h3>
            {headerExtra}
          </div>
          <IconButton label={closeLabel} name="close" onClick={onClose} size={16} />
        </header>
        <div className="scax-drawer__body">{children}</div>
        {footer && <footer className="scax-drawer__foot">{footer}</footer>}
      </section>
    </div>
  );
}

/**
 * Modal — 화면 한가운데 서는 오버레이. `Drawer` 와 같은 자리(`label`·`title`·`footer`·`onClose`)를
 * 받고 껍데기만 다르다: `.scax-modal-overlay` / `.scax-modal` / `__head` / `__title` / `__body` / `__foot`.
 *
 * **바퀴 6bc 에서 새로 만들었다.** 지금까지 이 골격을 쓰는 자리는 `ConfirmModal`(420) 하나뿐이라
 * 부품이 없었는데, §8-B 14 가 「업무 생성」을 서랍에서 모달로 옮기며 일반 골격이 필요해졌다.
 *
 * **폭은 부르는 쪽이 정한다** — 기본 880(`.scax-modal`) · `md` 560 · `sm` 420. 필드가 적은 자리
 * (회의록의 승격 = 요청 전용)는 `md`, 두 모드·체크리스트·프로젝트·참조가 다 서는 자리는 기본이다.
 * 골격 분기 없이 modifier 하나로 갈린다.
 *
 * 닫는 길 셋을 다 갖는다 — 머리의 × · Esc(`useEscape`, 겹 스택을 지킨다) · 바깥 클릭.
 */
export function Modal({
  label,
  title,
  headerExtra,
  footer,
  onClose,
  size,
  closeLabel,
  children,
}: {
  label: string;
  title: string;
  headerExtra?: ReactNode;
  footer?: ReactNode;
  onClose: () => void;
  /** 안 주면 DS 기본 880 이다. */
  size?: "sm" | "md";
  /** 닫기 단추의 이름 (바퀴 11) */
  closeLabel: string;
  children: ReactNode;
}) {
  useEscape(onClose);
  return (
    <div
      className="scax-modal-overlay"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        aria-label={label}
        aria-modal="true"
        className={size ? `scax-modal scax-modal--${size}` : "scax-modal"}
        role="dialog"
      >
        <header className="scax-modal__head">
          <div style={{ flex: "1 1 auto", minWidth: 0 }}>
            <h3 className="scax-modal__title">{title}</h3>
            {headerExtra}
          </div>
          <IconButton label={closeLabel} name="close" onClick={onClose} size={16} />
        </header>
        <div className="scax-modal__body">{children}</div>
        {footer && <footer className="scax-modal__foot">{footer}</footer>}
      </section>
    </div>
  );
}

/**
 * 되돌리기 어려운 결정 하나를 묻는 자리.
 *
 * 바퀴 3b 에서 새 DS 골격으로 갔다 — `.scax-modal-overlay` / `.scax-modal--sm`(420) / `__head` /
 * `__title` / `__body` / `__text` / `__foot`. DS 가 이 크기를 `role="alertdialog"` 로 규정하고
 * 있어서 우리가 이미 쓰던 role 과 같다.
 *
 * **바퀴 6bc 에서 넓힌 것 하나 (E-3)** — `cancelLabel`. 회의 삭제 확인(T08)은 «갈래를 두지 않는»
 * 자리라 아래에 [취소]를 두지 않는다. `cancelLabel={null}` 이면 푸터에 위험 단추 하나만 서고,
 * 대신 **머리에 × 가 선다** — 닫을 길은 반드시 남는다(× · Esc · 바깥 클릭 셋). 안 넘기면 예전과 똑같다.
 */
export function ConfirmModal({
  title,
  description,
  confirmLabel,
  cancelLabel,
  danger = false,
  busy = false,
  onConfirm,
  onClose,
  closeLabel,
}: {
  title: string;
  description: string;
  confirmLabel: string;
/** `null` 이면 푸터의 취소를 접고 머리에 닫기 × 를 세운다. 부르는 쪽이 늘 준다 (바퀴 11) */
  cancelLabel: string | null;
  /** 취소를 접었을 때 머리에 서는 × 의 이름 */
  closeLabel: string;
  danger?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}) {
  useEscape(onClose);
  return (
    <div className="scax-modal-overlay" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section aria-label={title} aria-modal="true" className="scax-modal scax-modal--sm" role="alertdialog">
        <header className="scax-modal__head">
          <h3 className="scax-modal__title">{title}</h3>
          {cancelLabel === null && <IconButton autoFocus label={closeLabel} name="close" onClick={onClose} size={16} />}
        </header>
        <div className="scax-modal__body">
          <p className="scax-modal__text">{description}</p>
        </div>
        <footer className="scax-modal__foot">
          {cancelLabel !== null && (
            <Button variant="text" disabled={busy} onClick={onClose} type="button">
              {cancelLabel}
            </Button>
          )}
          <Button disabled={busy} onClick={onConfirm} tone={danger ? "danger" : "primary"} type="button" variant="solid">
            {confirmLabel}
          </Button>
        </footer>
      </section>
    </div>
  );
}

/**
 * 알림 한 줄. v2 `14 — OVERLAY`: 400 × 56 · 하단 중앙 60px 위 · 4초.
 *
 * v2 는 「되돌릴 수 있는 동작이면 **실행취소를 함께** 둡니다」 라고 한다. 그래서 자리를 받아 두었지만
 * 지금 넘겨 주는 곳은 없다 — 이 제품의 상태 변경은 앞으로 가는 command 뿐이고, 되돌리기는 반대
 * command 를 새로 보내는 일이라 「실행취소」가 아니다. 되돌릴 수 있는 호출이 생기면 여기에 붙인다.
 *
 * `tone` 은 v2 에 없는 확장이다. 글 한 줄만으로는 완료와 실패가 같은 모양이라, 규격 안에 있는 글리프
 * 두 개(`07`)로만 구분한다 — 완료는 `--accent`(v2 16-2 는 이 값을 「액션과 **완료**에만」 쓰라고 한다), 실패는
 * `--danger-accent`. 초록은 이 시스템에 없는 축이라 쓰지 않고, 새 글리프도 만들지 않는다.
 * tone 을 주지 않으면 예전과 똑같이 그린다.
 *
 * 바퀴 3b: 껍데기가 `.scax-toast` / `.scax-toast__text` 로 갔다(핸드오프 `work-modal.jsx` 의 `Toast`).
 * 핸드오프 것은 3초 뒤 사라지는 «알림 한 줄» 뿐이라 우리 `action`(실행취소 자리)·`tone`·닫기 단추가 없다 —
 * 그 셋은 우리 것을 지켰다(F-3). `aria-live="polite"` 는 핸드오프에서 가져온 것이다.
 */
export function Toast({
  message,
  onClose,
  action,
  tone,
  closeLabel,
}: {
  message: string;
  onClose: () => void;
  action?: { label: string; onAction: () => void };
  tone?: "success" | "error";
  /** 알림을 지우는 단추의 이름 (바퀴 11: 부품은 말을 모른다) */
  closeLabel: string;
}) {
  useEffect(() => {
    const timer = window.setTimeout(onClose, 4000);
    return () => window.clearTimeout(timer);
  }, [message, onClose]);
  return (
    // 실패는 읽던 자리를 끊고 알려야 한다 — 그때만 role 을 alert 로 올린다.
    <div
      aria-live={tone === "error" ? undefined : "polite"}
      className={tone ? `scax-toast ${tone}` : "scax-toast"}
      role={tone === "error" ? "alert" : "status"}
    >
      {tone ? (
        <span className="scax-toast__text toast-message">
          <Icon className="toast-icon" name={tone === "success" ? "check" : "circle-exclamation"} />
          {message}
        </span>
      ) : (
        message
      )}
      {action && (
        <button
          className="toast-action"
          onClick={() => {
            action.onAction();
            onClose();
          }}
          type="button"
        >
          {action.label}
        </button>
      )}
      <button aria-label={closeLabel} onClick={onClose} type="button">
        <Icon name="close" />
      </button>
    </div>
  );
}
