import { useEffect, useRef, type ReactNode } from "react";
import { Icon } from "./Icon";

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

/** Drawer (840): editing and detail surfaces that need the page context behind them. */
export function Drawer({
  label,
  kicker,
  title,
  headerExtra,
  footer,
  onClose,
  children,
}: {
  label: string;
  kicker?: string;
  title: string;
  headerExtra?: ReactNode;
  footer?: ReactNode;
  onClose: () => void;
  children: ReactNode;
}) {
  useEscape(onClose);
  return (
    <>
      <div aria-hidden className="overlay-scrim" onMouseDown={onClose} />
      <section aria-label={label} aria-modal="true" className="drawer" role="dialog">
        <header className="drawer-head">
          <div style={{ minWidth: 0 }}>
            {kicker && <small className="modal-kicker">{kicker}</small>}
            <h3>{title}</h3>
            {headerExtra}
          </div>
          <button aria-label="상세 닫기" className="modal-close" onClick={onClose} type="button">
            <Icon name="close" />
          </button>
        </header>
        <div className="drawer-body">{children}</div>
        {footer && <footer className="drawer-foot">{footer}</footer>}
      </section>
    </>
  );
}

/** Modal (600): one hard-to-undo decision. */
export function ConfirmModal({
  title,
  description,
  confirmLabel,
  danger = false,
  busy = false,
  onConfirm,
  onClose,
}: {
  title: string;
  description: string;
  confirmLabel: string;
  danger?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}) {
  useEscape(onClose);
  return (
    <div className="modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section aria-label={title} aria-modal="true" className="modal" role="alertdialog">
        <header className="modal-head">
          <h3>{title}</h3>
        </header>
        <div className="modal-body">
          <p>{description}</p>
        </div>
        <footer className="modal-foot">
          <button className="btn h40 ghost" disabled={busy} onClick={onClose} type="button">
            돌아가기
          </button>
          <button className={danger ? "btn h40 danger" : "btn h40 primary"} disabled={busy} onClick={onConfirm} type="button">
            {confirmLabel}
          </button>
        </footer>
      </section>
    </div>
  );
}

/**
 * 알림 한 줄. v2 `14 — OVERLAY`: 400 × 56 · 하단 중앙 60px 위 · 4초.
 *
 * v2 는 "되돌릴 수 있는 동작이면 **실행취소를 함께** 둡니다" 라고 한다. 그래서 자리를 받아 두었지만
 * 지금 넘겨 주는 곳은 없다 — 이 제품의 상태 변경은 앞으로 가는 command 뿐이고, 되돌리기는 반대
 * command 를 새로 보내는 일이라 "실행취소"가 아니다. 되돌릴 수 있는 호출이 생기면 여기에 붙인다.
 *
 * `tone` 은 v2 에 없는 확장이다. 글 한 줄만으로는 완료와 실패가 같은 모양이라, 규격 안에 있는 글리프
 * 두 개(`07`)로만 구분한다 — 완료는 `--accent`(v2 16-2 는 이 값을 "액션과 **완료**에만" 쓰라고 한다), 실패는
 * `--danger-accent`. 초록은 이 시스템에 없는 축이라 쓰지 않고, 새 글리프도 만들지 않는다.
 * tone 을 주지 않으면 예전과 똑같이 그린다.
 */
export function Toast({
  message,
  onClose,
  action,
  tone,
}: {
  message: string;
  onClose: () => void;
  action?: { label: string; onAction: () => void };
  tone?: "success" | "error";
}) {
  useEffect(() => {
    const timer = window.setTimeout(onClose, 4000);
    return () => window.clearTimeout(timer);
  }, [message, onClose]);
  return (
    // 실패는 읽던 자리를 끊고 알려야 한다 — 그때만 role 을 alert 로 올린다.
    <div className={tone ? `toast ${tone}` : "toast"} role={tone === "error" ? "alert" : "status"}>
      {tone ? (
        <span className="toast-message">
          <Icon className="toast-icon" name={tone === "success" ? "check" : "alert"} />
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
      <button aria-label="알림 지우기" onClick={onClose} type="button">
        <Icon name="close" />
      </button>
    </div>
  );
}
