import { useEffect, type ReactNode } from "react";

function useEscape(onClose: () => void) {
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);
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
            ×
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

export function Toast({ message, onClose }: { message: string; onClose: () => void }) {
  useEffect(() => {
    const timer = window.setTimeout(onClose, 4000);
    return () => window.clearTimeout(timer);
  }, [message, onClose]);
  return (
    <div className="toast" role="status">
      {message}
      <button aria-label="알림 지우기" onClick={onClose} type="button">
        ×
      </button>
    </div>
  );
}
