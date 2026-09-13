import type React from "react";

/**
 * 읽는 표 — 열 수에 안 묶인다 (바퀴 9 · `DS-gaps` G-15 · G-46).
 *
 * **새 DS 에 없어서 우리가 새 DS 어휘로 만들었다.** DS 의 `.scax-task-table` 은 업무 표 전용
 * 6열 격자(첫 트랙이 별표)라 열이 제각각인 표가 쓸 수 없다 — 변경 기록(5열) · 공유 대상(3열) ·
 * 보낸 업무(4열)가 각자 다르다. 구 `.plain-table` 을 그대로 옮기고 이름만 `.scax-table` 로 바꿨다.
 *
 * `.scax-task-table` 과 달리 **시맨틱 `<table>`** 이다. 읽는 표는 격자 흉내를 낼 이유가 없고,
 * 열 수가 화면마다 다르면 CSS 격자 쪽이 오히려 매번 변형을 만들어야 한다.
 *
 * 칸 정렬은 `align`(가운데·오른쪽)으로, 「제목 칸」은 `title` 로 준다 — 구 `.center`/`.end`/
 * `.title-cell` 이 하던 일이고, 이름만 `.scax-table__*` 로 바뀌었다.
 */
export type CellAlign = "start" | "center" | "end";

function cellClass(align: CellAlign | undefined, title: boolean | undefined, className: string | undefined) {
  const classes: string[] = [];
  if (align === "center") classes.push("scax-table__center");
  if (align === "end") classes.push("scax-table__end");
  if (title) classes.push("scax-table__title");
  if (className) classes.push(className);
  return classes.length > 0 ? classes.join(" ") : undefined;
}

export function DataTable({ label, children, className }: { label?: string; children: React.ReactNode; className?: string }) {
  return (
    <table aria-label={label} className={["scax-table", className ?? ""].filter(Boolean).join(" ")}>
      {children}
    </table>
  );
}

export function Th({
  align,
  scope = "col",
  children,
  className,
  ...rest
}: {
  align?: CellAlign;
  scope?: "col" | "row";
  children?: React.ReactNode;
  className?: string;
} & Omit<React.ThHTMLAttributes<HTMLTableCellElement>, "className" | "scope" | "align">) {
  return (
    <th {...rest} className={cellClass(align, false, className)} scope={scope}>
      {children}
    </th>
  );
}

export function Td({
  align,
  title,
  children,
  className,
  ...rest
}: {
  align?: CellAlign;
  /** 표에서 폭을 다 먹고 줄바꿈이 허용되는 「제목 칸」 하나. */
  title?: boolean;
  children?: React.ReactNode;
  className?: string;
} & Omit<React.TdHTMLAttributes<HTMLTableCellElement>, "className" | "title" | "align">) {
  return (
    <td {...rest} className={cellClass(align, title, className)}>
      {children}
    </td>
  );
}

/** 누르면 열리는 줄. 누르는 자리가 아니면 그냥 `<tr>` 을 쓴다. */
export function TrOpenable({
  onClick,
  children,
  className,
  ...rest
}: {
  onClick: React.MouseEventHandler<HTMLTableRowElement>;
  children?: React.ReactNode;
  className?: string;
} & Omit<React.HTMLAttributes<HTMLTableRowElement>, "className" | "onClick">) {
  return (
    <tr {...rest} className={["scax-table__row--openable", className ?? ""].filter(Boolean).join(" ")} onClick={onClick}>
      {children}
    </tr>
  );
}
