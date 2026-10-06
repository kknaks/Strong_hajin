import { useEffect, useState, type ReactNode } from "react";

import { Button, IconButton } from "../../ds/Button";
import { Empty } from "../../ds/Empty";
import { Icon } from "../../ds/icons/Icon";
import { useEscape } from "../../ds/Modal";
import { SegmentedControl } from "../../ds/SegmentedControl";
import { settingsScreen as copy } from "../../lib/labels";

/**
 * 방 고르기 창 — 슬랙 「방 추가」와 카톡 「카카오톡 방 추가」가 같은 틀을 쓴다 (시안 `RoomPicker`·`KakaoPicker` · DC-2·6).
 *
 * 검색 · 묶음 탭(전체에선 묶음별 구획 + 개수) · 줄(체크 · 표식 · 이름 · 봇이면 `앱` · 인원) · 이미 넣은 방 「추가됨」 ·
 * 「선택한 N개 추가」. 상태 넷: 기본 · 로딩 · 결과 없음(검색) · 오류 — 카톡은 «꺼짐/앱 없음» 안내가 더 있다.
 * 목록을 어디서 받는지(슬랙 = 서버가 사용자 토큰으로 · 카톡 = 데스크톱 앱이 로컬에서)는 부르는 쪽이 정한다.
 */

export type PickRow = { id: string; group: string; name: string; isBot?: boolean; meta: string; added: boolean; mark: ReactNode };

export type PickState = "loading" | "error" | "ready" | { kind: "notice"; title: string; desc: string };

export function RoomPicker<T extends string>({
  title,
  tabs,
  groupOrder,
  groupLabel,
  rows,
  state,
  errorDesc,
  query,
  onQuery,
  hint,
  onClose,
  onAdd,
  onRetry,
  busy,
  hasMore,
  onMore,
}: {
  title: string;
  tabs: ReadonlyArray<{ value: T | "all"; label: string }>;
  groupOrder: readonly T[];
  groupLabel: (group: T) => string;
  rows: PickRow[];
  state: PickState;
  errorDesc: string;
  query: string;
  onQuery: (query: string) => void;
  hint: string;
  onClose: () => void;
  onAdd: (ids: string[]) => void;
  onRetry: () => void;
  busy: boolean;
  hasMore?: boolean;
  onMore?: () => void;
}) {
  const [tab, setTab] = useState<T | "all">("all");
  const [picked, setPicked] = useState<string[]>([]);
  useEscape(onClose);
  useEffect(() => {
    setPicked([]);
  }, [title]);

  const q = query.trim();
  const shown = rows.filter((row) => (tab === "all" || row.group === tab) && (!q || row.name.includes(q)));
  const toggle = (id: string) => setPicked((current) => (current.includes(id) ? current.filter((item) => item !== id) : [...current, id]));
  const groups = tab === "all" ? groupOrder : groupOrder.filter((group) => group === tab);
  const ready = state === "ready";

  let body: ReactNode;
  if (typeof state === "object") {
    body = <Empty description={state.desc} icon="blank" title={state.title} />;
  } else if (state === "loading") {
    body = (
      <div aria-busy="true" className="scax-skeleton-stack" role="status">
        {[0, 1, 2, 3, 4].map((index) => (
          <div className="scax-set-pick__skel" key={index}>
            <span aria-hidden className="scax-skeleton scax-skeleton--text scax-skeleton--w-full" />
          </div>
        ))}
      </div>
    );
  } else if (state === "error") {
    body = <Empty actionLabel={copy.retry} description={errorDesc} onAction={onRetry} title={copy.pickError} variant="error" />;
  } else if (!shown.length) {
    body = <Empty description={q ? copy.pickNoneDesc(q) : undefined} icon="search" title={copy.pickNone} variant="filter" />;
  } else {
    body = (
      <>
        {groups.map((group) => {
          const list = shown.filter((row) => row.group === group);
          if (!list.length) return null;
          return (
            <section className="scax-set-pick__group" key={group}>
              {tab === "all" ? (
                <h3 className="scax-set-pick__caption">
                  {groupLabel(group)} <span className="scax-set-pick__count">{list.length}</span>
                </h3>
              ) : null}
              <ul className="scax-set-pick__list">
                {list.map((row) => {
                  const on = picked.includes(row.id);
                  return (
                    <li key={row.id}>
                      <label className={`scax-set-pick__row${row.added ? " scax-set-pick__row--added" : ""}${on ? " scax-set-pick__row--on" : ""}`}>
                        <span className="scax-checkbox">
                          <input
                            aria-label={row.name}
                            checked={row.added || on}
                            className="scax-checkbox__input"
                            disabled={row.added}
                            onChange={() => toggle(row.id)}
                            type="checkbox"
                          />
                          <span className="scax-checkbox__box">
                            <Icon name="check" size={14} />
                          </span>
                        </span>
                        {row.mark}
                        <span className="scax-set-pick__name">{row.name}</span>
                        {row.isBot ? <span className="scax-set-app">{copy.app}</span> : null}
                        <span className="scax-set-pick__meta">{row.meta}</span>
                        {row.added ? <span className="scax-set-pick__added">{copy.added}</span> : null}
                      </label>
                    </li>
                  );
                })}
              </ul>
            </section>
          );
        })}
        {hasMore && onMore ? (
          <div className="scax-inbox-list__more">
            <Button label={copy.more} onClick={onMore} size="sm" variant="text" />
          </div>
        ) : null}
      </>
    );
  }

  return (
    <div
      className="scax-modal-overlay"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div aria-label={title} aria-modal="true" className="scax-modal scax-modal--md scax-set-pick" role="dialog">
        <header className="scax-modal__head">
          <h2 className="scax-modal__title">{title}</h2>
          <IconButton label={copy.close} name="close" onClick={onClose} />
        </header>
        <div className="scax-set-pick__tools">
          <div className="scax-textfield scax-textfield--search">
            <Icon name="search" size={20} />
            <input
              aria-label={copy.pickSearchAria}
              className="scax-textfield__input"
              disabled={typeof state === "object"}
              onChange={(event) => onQuery(event.target.value)}
              placeholder={copy.pickSearch}
              value={query}
            />
          </div>
          <SegmentedControl ariaLabel={copy.pickKind} onChange={setTab} options={tabs} value={tab} />
        </div>
        <div className="scax-modal__body scax-set-pick__body">{body}</div>
        <footer className="scax-modal__foot">
          <span className="scax-set-pick__hint">{hint}</span>
          <Button label={copy.cancel} onClick={onClose} tone="neutral" variant="outlined" />
          <Button
            disabled={!picked.length || !ready || busy}
            label={copy.pickAdd(picked.length)}
            onClick={() => onAdd(picked)}
            tone="primary"
            variant="solid"
          />
        </footer>
      </div>
    </div>
  );
}
