import { useEffect, useRef, type ReactNode } from "react";

/**
 * 왼쪽에 고정폭 메타 칸(시각 · 누구 · 종류)을 두고 오른쪽에 본문을 흘리는 목록.
 *
 * 디자인 시스템 v2 에 이 부품이 없다. `.plain-table` 은 칸이 정해진 표이고 `.meeting-list` 는 한 줄 행이라,
 * 「같은 시각 축에 종류가 다른 줄이 섞여 서는」 자리(원문 · 활동 기록)를 담을 것이 없었다.
 * `muted` 는 아직 굳지 않은 줄이다 — 저장되지 않았음을 색으로만 말한다.
 */
export type GutterRow = {
  key: string;
  gutter: ReactNode;
  /** 본문 앞에 서는 둘째 고정 칸 — 없으면 칸 자체가 서지 않는다. */
  aside?: ReactNode;
  body: ReactNode;
  muted?: boolean;
  active?: boolean;
};

export function GutterList({
  label,
  rows,
  gutterWidth = 76,
  asideWidth = 72,
}: {
  label: string;
  rows: GutterRow[];
  gutterWidth?: number;
  asideWidth?: number;
}) {
  const first = useRef<HTMLLIElement | null>(null);
  // 다른 자리에서 이 목록의 한 줄을 가리켰다 — 그 줄이 눈에 들어와야 가리킨 값이 있다.
  const activeKey = rows.find((row) => row.active)?.key ?? null;
  useEffect(() => {
    // jsdom 처럼 스크롤이 없는 환경도 있다 — 없으면 켜지기만 하고 넘어간다.
    if (activeKey) first.current?.scrollIntoView?.({ block: "center" });
  }, [activeKey]);
  return (
    <ul aria-label={label} className="gutter-list">
      {rows.map((row) => (
        <li
          className={row.active ? "gutter-row active" : "gutter-row"}
          key={row.key}
          ref={row.key === activeKey ? first : undefined}
        >
          <span className="gutter-meta" style={{ width: gutterWidth }}>
            {row.gutter}
          </span>
          {row.aside !== undefined && (
            <span className="gutter-aside" style={{ width: asideWidth }}>
              {row.aside}
            </span>
          )}
          <span className={row.muted ? "gutter-body muted" : "gutter-body"}>{row.body}</span>
        </li>
      ))}
    </ul>
  );
}
