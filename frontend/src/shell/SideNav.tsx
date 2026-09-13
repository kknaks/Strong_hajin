import type React from "react";

import { Icon, type IconName } from "../ds/icons/Icon";

/**
 * 왼쪽 내비 기둥 (바퀴 2).
 *
 * 원본은 새 DS 의 `design/components/shell/SideNav.jsx`, 타입 정본은 `SideNav.d.ts`.
 * 마크업·클래스는 그대로 옮겼다. 원본에서 달라진 곳이 셋이고 전부 이유가 있다.
 *
 * ① `onSelect` 를 더했다. DS 는 `href` 가 있는 항목만 이동하고 없으면 `aria-disabled` 로 죽는데,
 *    우리 화면 전환은 URL 이 아니라 `App.tsx` 의 `surface` 상태다(라우터가 없다). `href` 를 못 주므로
 *    DS 규칙 그대로면 여덟 개가 전부 눌리지 않는다. `href` 가 없고 `onSelect` 가 있으면 `<button>`
 *    으로 서고, 둘 다 없을 때만 DS 대로 `aria-disabled="true"` 로 선다.
 * ② `label`(접근성 이름)을 prop 으로 뺐다. 기본값은 DS 의 「주 메뉴」지만 우리는 기존 이름
 *    「제품 탐색」을 그대로 쓴다 — 우리 카피이고 화면·테스트가 이 이름으로 이 영역을 찾는다.
 * ③ 신원 줄에 `onUserClick` 을 더했다. 구 셸에서 프로필을 누르면 「내 AX 캐릭터」가 열렸고
 *    그 경로가 화면·테스트에 살아 있다. DS 의 신원 줄은 누를 수 없어서 그대로 옮기면 길이 끊긴다.
 * ④ 아바타는 `user.avatar` 가 있을 때만 그린다. DS 는 무조건 `<img src={user.avatar}>` 인데
 *    우리 세션에는 아바타 URL 이 없어서 그대로 두면 깨진 이미지가 뜬다 (없는 값을 지어내지 않는다).
 */

/** 내비 한 줄. `href` 도 `onSelect` 도 없으면 비활성으로 서고 이동하지 않는다. */
export interface NavItem {
  id: string;
  label: string;
  /** `Icon` 의 글리프 이름. */
  icon: IconName;
  href?: string;
  /** 안 읽은 것이 있으면 글리프 오른쪽 위에 점이 선다. */
  dot?: boolean;
}

export interface SideNavProps {
  user?: { name: string; role?: string; avatar?: string };
  /** 주 메뉴. */
  items?: NavItem[];
  /** 구분선 위에 서는 알림·설정 같은 항목. */
  utilityItems?: NavItem[];
  activeId?: string;
  /** 접힘(65px). 아이콘만 남고 hover 에서 라벨 툴팁이 뜬다. */
  collapsed?: boolean;
  onCollapse?: () => void;
  /** 항목을 골랐을 때. `href` 가 없는 항목은 이것으로 움직인다. */
  onSelect?: (id: string) => void;
  /** 신원 줄을 눌렀을 때. 없으면 DS 대로 누를 수 없는 글자로 선다. */
  onUserClick?: () => void;
  /** `onUserClick` 이 있을 때 그 단추의 접근성 이름. */
  userActionLabel?: string;
  /** 바닥 워드마크 «위» 에 서는 계정 행동들(설정·로그아웃). 접히면 글자가 들어갈 자리가 없어 감춘다. */
  footerActions?: React.ReactNode;
  /** 이 영역의 접근성 이름. */
  label?: string;
  /** 바닥의 워드마크. 접히면 첫 글자만 선다. */
  logo?: string;
  version?: string;
}

export function SideNav({
  user,
  items = [],
  utilityItems = [],
  activeId,
  collapsed,
  onCollapse,
  onSelect,
  onUserClick,
  userActionLabel,
  footerActions,
  label = "주 메뉴",
  logo = "LOGO",
  version,
}: SideNavProps) {
  const renderItem = (item: NavItem) => {
    const active = item.id === activeId;
    const className = `scax-nav-item${active ? " scax-nav-item--active" : ""}`;
    const inside = (
      <>
        <span className="scax-nav-item__glyph">
          <Icon name={item.icon} size={20} />
          {item.dot ? <span className="scax-nav-item__dot" /> : null}
        </span>
        {collapsed ? null : <span className="scax-nav-item__label">{item.label}</span>}
      </>
    );
    if (!item.href && onSelect) {
      return (
        <button
          aria-current={active ? "page" : undefined}
          className={className}
          data-label={item.label}
          key={item.id}
          onClick={() => onSelect(item.id)}
          type="button"
        >
          {inside}
        </button>
      );
    }
    return (
      <a
        aria-current={active ? "page" : undefined}
        aria-disabled={item.href ? undefined : "true"}
        className={className}
        data-label={item.label}
        href={item.href || undefined}
        key={item.id}
      >
        {inside}
      </a>
    );
  };

  return (
    <nav aria-label={label} className={`scax-side-nav${collapsed ? " scax-side-nav--collapsed" : ""}`}>
      {/* 접으면 이 줄이 세로로 서고, 접기 단추가 아바타 위로 올라간다 (디자이너 확정) */}
      <div className="scax-side-nav__identity">
        <button
          aria-expanded={!collapsed}
          aria-label={collapsed ? "메뉴 펴기" : "메뉴 접기"}
          className="scax-side-nav__collapse"
          onClick={onCollapse}
          type="button"
        >
          <Icon name="left-side" size={20} />
        </button>
        {user?.avatar ? <img alt="" className="scax-side-nav__avatar" src={user.avatar} /> : null}
        {user && !collapsed
          ? (() => {
              const who = (
                <>
                  <span className="scax-side-nav__name">{user.name}</span>
                  {user.role ? (
                    <span className="scax-side-nav__role" title={user.role}>
                      {user.role}
                    </span>
                  ) : null}
                </>
              );
              return onUserClick ? (
                <button aria-label={userActionLabel} className="scax-side-nav__who" onClick={onUserClick} type="button">
                  {who}
                </button>
              ) : (
                <span className="scax-side-nav__who">{who}</span>
              );
            })()
          : null}
      </div>
      {utilityItems.length ? <div className="scax-side-nav__group">{utilityItems.map(renderItem)}</div> : null}
      {utilityItems.length ? <div className="scax-side-nav__divider" /> : null}
      <div className="scax-side-nav__group">{items.map(renderItem)}</div>
      <div className="scax-side-nav__spacer" />
      {footerActions && !collapsed ? <div className="scax-side-nav__actions">{footerActions}</div> : null}
      <div className="scax-side-nav__footer">
        <span className="scax-side-nav__logo">{collapsed ? logo.slice(0, 1) : logo}</span>
        {/* 버전은 우리가 들고 있는 값이 없으면 안 그린다 */}
        {collapsed || !version ? null : <span className="scax-side-nav__version">{version}</span>}
      </div>
    </nav>
  );
}

export default SideNav;
