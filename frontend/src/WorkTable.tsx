import type { MouseEvent, ReactNode } from "react";

type Column = { label: string; width?: string; align?: "start" | "center" | "end" };

const defaultColumns: Column[] = [
  { label: "업무명" },
  { label: "상태", width: "140px", align: "center" },
  { label: "액션", width: "260px", align: "end" },
];

/** Table (h44 header / h56 rows) inside a r12 outline. Use `variant="list"` for h68 rows without a header. */
export function WorkTable({
  columns = defaultColumns,
  children,
  as: Tag = "div",
  empty,
  variant = "table",
}: {
  columns?: Column[];
  children: ReactNode;
  as?: "div" | "ul";
  empty?: string | null;
  variant?: "table" | "list";
}) {
  if (variant === "list") {
    return empty ? <p className="empty-row">{empty}</p> : <Tag className="work-table-body list-rows">{children}</Tag>;
  }
  const template = columns.map((column) => column.width ?? "minmax(0, 1fr)").join(" ");
  return (
    <div className="work-table" style={{ ["--columns" as string]: template }}>
      <div aria-hidden className="work-table-head">
        {columns.map((column) => (
          <span className={column.align === "end" ? "end" : column.align === "center" ? "center" : ""} key={column.label}>
            {column.label}
          </span>
        ))}
      </div>
      {empty ? <p className="empty-row">{empty}</p> : <Tag className="work-table-body">{children}</Tag>}
    </div>
  );
}

export function WorkRow({
  as: Tag = "article",
  title,
  titleClassName,
  subtitle,
  subtitleTone,
  status,
  actions,
  onOpen,
  extra,
  selected = false,
  ...rest
}: {
  as?: "article" | "li";
  title: string;
  titleClassName?: string;
  subtitle?: string | null;
  subtitleTone?: "danger" | "muted";
  status: ReactNode;
  actions?: ReactNode;
  onOpen?: () => void;
  extra?: ReactNode;
  selected?: boolean;
  [key: `data-${string}`]: string | undefined;
}) {
  const open = (event: MouseEvent) => {
    if (!onOpen) return;
    if ((event.target as HTMLElement).closest("button, input, select, textarea, a, label")) return;
    onOpen();
  };
  const className = ["progress-row", onOpen ? "openable" : "", selected ? "selected" : ""].filter(Boolean).join(" ");
  return (
    <Tag className={className} onClick={open} {...rest}>
      <div className="cell-main">
        <b className={titleClassName}>{title}</b>
        {subtitle && <small className={subtitleTone === "danger" ? "reason" : ""}>{subtitle}</small>}
      </div>
      <div className="cell-status">{status}</div>
      <div className="cell-actions task-actions">
        {actions}
        {onOpen && (
          <button className="btn h30 ghost" onClick={onOpen} type="button">
            상세보기
          </button>
        )}
      </div>
      {extra}
    </Tag>
  );
}
