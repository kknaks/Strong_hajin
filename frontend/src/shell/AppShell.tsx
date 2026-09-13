import type React from "react";

/**
 * 페이지 틀 — `AppShell` · `AppHeader` · `AppBody` 셋 (바퀴 2).
 *
 * 원본은 새 DS 의 `design/components/shell/AppShell.jsx`. 마크업은 그대로 옮겼고
 * 타입은 옆자리 `AppShell.d.ts` 가 정본이다. 스타일은 `src/styles/shell.css` 가 가진다.
 *
 * 원본에서 넓힌 곳이 하나 있다 — `breadcrumb` 이 `string[]` 이 아니라 `ReactNode[]` 다.
 * DS 는 브레드크럼을 「더 이상 쓰지 않는다」고 적어 두었지만(`AppShell.d.ts:15`),
 * 우리는 회의 상세에서 목록으로 돌아가는 유일한 길이 이 줄이라 과도기에는 살려 둔다(바퀴 2 D-A).
 * 누를 수 있어야 하므로 항목이 문자열만이면 안 된다. `string[]` 은 그대로 들어간다.
 * 브레드크럼 삭제는 바퀴 6 이다.
 */

export interface AppShellProps {
  /** `SideNav` 를 넣는다. */
  nav?: React.ReactNode;
  children?: React.ReactNode;
}

export interface AppHeaderProps {
  /** 과도기에만 쓴다 (바퀴 2 D-A) — 바퀴 6 에서 사라진다. */
  breadcrumb?: React.ReactNode[];
  /** 그 탭의 이름. 왼쪽 끝에 선다. */
  title?: React.ReactNode;
  /** 오른쪽 끝의 단추 둘. 왼쪽이 outlined, 오른쪽이 solid. */
  actions?: React.ReactNode;
}

export interface AppBodyProps {
  railLeft?: React.ReactNode;
  railRight?: React.ReactNode;
  children?: React.ReactNode;
}

export function AppShell({ nav, children }: AppShellProps) {
  return (
    <div className="scax-app-shell">
      {nav}
      <div className="scax-app-main">{children}</div>
    </div>
  );
}

export function AppHeader({ breadcrumb, title, actions }: AppHeaderProps) {
  return (
    <header className="scax-page-header">
      <div className="scax-page-header__lead">
        {breadcrumb && breadcrumb.length ? (
          <nav aria-label="현재 위치" className="scax-breadcrumb">
            {breadcrumb.map((crumb, index) => [
              index > 0 ? (
                <span aria-hidden className="scax-breadcrumb__sep" key={`sep-${index}`}>
                  /
                </span>
              ) : null,
              <span
                className={`scax-breadcrumb__item${index === breadcrumb.length - 1 ? " scax-breadcrumb__item--current" : ""}`}
                key={`crumb-${index}`}
              >
                {crumb}
              </span>,
            ])}
          </nav>
        ) : null}
        <h1 className="scax-page-header__title">{title}</h1>
      </div>
      <div className="scax-page-header__actions">{actions}</div>
    </header>
  );
}

export function AppBody({ railLeft, railRight, children }: AppBodyProps) {
  return (
    <div className="scax-page-body">
      {railLeft ? <aside className="scax-page-body__rail scax-page-body__rail--left">{railLeft}</aside> : null}
      <main className="scax-page-body__content">{children}</main>
      {railRight ? <aside className="scax-page-body__rail scax-page-body__rail--right">{railRight}</aside> : null}
    </div>
  );
}

export default AppShell;
