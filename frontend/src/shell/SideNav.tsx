import type React from "react";

import { Avatar } from "../ds/Avatar";
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
  /**
   * **진짜로 못 누르는 줄.** `<button disabled>` 로 서서 클릭도 키보드 실행도 안 되고 탭 순서에서도 빠진다.
   * `aria-disabled` 만 붙여 «보기에만» 꺼진 줄과 다르다 — 갈 화면이 없는 자리(알림)가 이것이다.
   */
  disabled?: boolean;
  /**
   * 이 줄만의 동작. 화면 전환(`onSelect`)이 아닌 자리가 쓴다 — 설정처럼 모달을 여는 줄이다.
   * 주면 `onSelect` 대신 이것이 불린다: 설정 id 를 화면 이름으로 넘겨 라우팅에 흘리지 않는다.
   */
  onActivate?: () => void;
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
    /* 접히면 글자가 빠지고 글리프만 남는데 그 글리프는 `aria-hidden` 이다 — 그대로 두면 이 줄에
       **읽어 줄 이름이 하나도 없다.** 눈으로는 hover 툴팁(`data-label`)이 말해 주지만 그것은 CSS 다.
       접힌 동안만 이름을 직접 붙인다 — 펴져 있을 때는 라벨 글자가 이미 그 일을 한다. */
    const srLabel = collapsed ? item.label : undefined;
    /* 못 누르는 줄은 «정말» 못 누르게 둔다 — `disabled` 가 클릭·Enter/Space·탭 순서를 한꺼번에 막는다.
       `aria-disabled` 만 붙이면 키보드로는 여전히 눌려서 「없는 기능」이 실행되는 길이 남는다. */
    if (item.disabled) {
      return (
        <button aria-label={srLabel} className={className} data-label={item.label} disabled key={item.id} type="button">
          {inside}
        </button>
      );
    }
    const activate = item.onActivate ?? (onSelect ? () => onSelect(item.id) : null);
    if (!item.href && activate) {
      return (
        <button
          aria-current={active ? "page" : undefined}
          aria-label={srLabel}
          className={className}
          data-label={item.label}
          key={item.id}
          onClick={activate}
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
        aria-label={srLabel}
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
        {/* 사진이 있으면 사진, 없으면 DS `Avatar` 의 첫 글자다 — 없는 이미지를 그려 깨진 아이콘을 내지 않는다.
            세션(`/api/auth/me`)에는 아직 사진 URL 이 없어서 실제로는 첫 글자가 선다 (보고 대상). */}
        {user?.avatar ? (
          <img alt="" className="scax-side-nav__avatar" src={user.avatar} />
        ) : user ? (
          <Avatar className="scax-side-nav__avatar" name={user.name} size="md" />
        ) : null}
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
