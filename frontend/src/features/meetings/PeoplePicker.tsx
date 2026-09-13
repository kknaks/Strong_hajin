import { meetingScreen } from "../../lib/labels";
import { Avatar } from "../../ds/Avatar";
import type { Roster, RosterPerson } from "./roster";
import { searchRoster } from "./roster";
import { CheckboxBox } from "../../ds/FormControls";

/**
 * 이름으로 찾는 자리 — 예약(MOD-102) · 공유(MOD-105) · 상세 머리 편집이 같은 모양을 쓴다.
 * 부서를 넘어 전부에서 찾고, 고른 사람은 결과에서 빠진다.
 *
 * 사외 참석자는 따로 칸을 두지 않는다 (MOD-102-E06) — 명부에 없는 이름을 쳤을 때 결과 아래에
 * 「{이름} · 사외 참석자로 추가」 한 줄이 서고, 그것을 누르면 태그가 된다. 계정을 만들지 않는다 (X-97).
 */
export function PersonSearch({
  roster,
  query,
  onQueryChange,
  excluded,
  onPick,
  placeholder,
  guest,
}: {
  roster: Roster | null;
  query: string;
  onQueryChange: (value: string) => void;
  excluded: ReadonlySet<string>;
  onPick: (person: RosterPerson) => void;
  placeholder: string;
  /** 사외 참석자를 받는 자리에만 온다. */
  guest?: { canAdd: (name: string) => boolean; onAdd: (name: string) => void };
}) {
  const text = query.trim();
  const hits = searchRoster(roster, query, excluded);
  const open = text.length > 0;
  const canAddGuest = Boolean(guest && text.length > 0 && text.length <= 30 && guest.canAdd(text));
  return (
    <div className="popover-root" style={{ display: "block", position: "relative" }}>
      <input
        aria-expanded={open}
        aria-label={placeholder}
        className="search-input-box"
        onChange={(event) => onQueryChange(event.target.value)}
        placeholder={placeholder}
        role="combobox"
        style={{ width: "100%" }}
        type="text"
        value={query}
      />
      {open && (
        <div aria-label="이름 찾기 결과" className="popover" role="listbox" style={{ left: 0, right: 0, width: "auto" }}>
          {hits.map((person) => (
            <button
              className="popover-item"
              key={person.member_id}
              onClick={() => onPick(person)}
              role="option"
              type="button"
            >
              <Avatar name={person.name} size="xs" />
              <span style={{ flex: 1, minWidth: 0, fontWeight: 700, color: "var(--scax-color-ink)" }}>{person.name}</span>
              <span className="t-meta" style={{ flex: "none", fontSize: 12 }}>
                {[person.unit, person.rank].filter(Boolean).join(" · ")}
              </span>
            </button>
          ))}
          {hits.length === 0 && !canAddGuest && (
            <div className="t-meta" style={{ padding: "8px 12px", fontSize: 13 }}>
              {meetingScreen.noMatch}
            </div>
          )}
          {/* 명부에 그 이름이 없을 때 — 그 사실을 말하는 줄이 먼저고, 사외로 다는 것은 그 아래 고르는 줄이다.
              줄 둘을 가로선으로 가른다. 결과가 있을 때는 아래의 평소 모양 그대로다 */}
          {hits.length === 0 && canAddGuest && guest && (
            <div className="popover-guest">
              <div className="popover-note">{meetingScreen.noEmployee}</div>
              <button className="popover-guest-add" onClick={() => guest.onAdd(text)} type="button">
                {`${text} ${meetingScreen.addGuest}`}
              </button>
            </div>
          )}
          {hits.length > 0 && canAddGuest && guest && (
            <button
              className="popover-item"
              onClick={() => guest.onAdd(text)}
              style={{ borderTop: "1px solid var(--scax-color-line-weak)", borderRadius: 0 }}
              type="button"
            >
              <Avatar name={text} size="xs" />
              <span style={{ flex: 1, minWidth: 0, fontWeight: 700, color: "var(--scax-color-ink)" }}>{text}</span>
              <span className="t-meta" style={{ flex: "none", fontSize: 12 }}>
                {meetingScreen.addGuest}
              </span>
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * 조직도 — 왼쪽 부서, 오른쪽 그 부서 사람. 위 검색이 부서를 넘어 찾는 동안 이쪽은 고른 부서만 낸다.
 *
 * 조직 화면의 `OrgTreePanel`·`MemberListPanel` 은 68 높이 행에 여섯 축을 펴는 전체 화면 패널이라
 * 모달 안에 들어가지 않는다. 여기서는 「고르기」 하나만 하는 얇은 2단이다.
 */
export function OrgDirectory({
  roster,
  unitId,
  onUnitChange,
  selected,
  onToggle,
  mode,
  emptyText,
  excluded,
}: {
  roster: Roster | null;
  unitId: string | null;
  onUnitChange: (unitId: string) => void;
  selected: ReadonlySet<string>;
  onToggle: (person: RosterPerson) => void;
  /** `check` 는 담고 빼는 토글(예약), `add` 는 누르면 담기기만 한다(공유). */
  mode: "check" | "add";
  emptyText?: string;
  excluded?: ReadonlySet<string>;
}) {
  const units = roster?.units ?? [];
  const active = unitId ?? units[0]?.id ?? null;
  const hidden = excluded ?? new Set<string>();
  const members = (roster?.people ?? []).filter((person) => person.unit_id === active && !hidden.has(person.member_id));
  /** 그 부서에서 **지금 고를 수 있는** 사람 수. 줄마다 «자기» 부서를 센다 — 고른 부서의 수가 아니다. */
  const countOf = (unitId: string) =>
    (roster?.people ?? []).filter((person) => person.unit_id === unitId && !hidden.has(person.member_id)).length;
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "180px minmax(0,1fr)",
        border: "1px solid var(--scax-color-line)",
        borderRadius: "var(--scax-radius-sm)",
        overflow: "hidden",
      }}
    >
      <div className="meeting-scroll" style={{ maxHeight: 216, overflowY: "auto", padding: 8, borderRight: "1px solid var(--scax-color-line)", background: "var(--scax-color-surface-alt)" }}>
        {units.map((unit) => {
          const on = unit.id === active;
          return (
            <button
              key={unit.id}
              onClick={() => onUnitChange(unit.id)}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                width: "100%",
                padding: "8px 12px",
                borderRadius: "var(--scax-radius-sm)",
                fontSize: 13,
                textAlign: "left",
                fontWeight: on ? 700 : 400,
                color: on ? "var(--scax-color-accent)" : "var(--scax-color-ink)",
                background: on ? "var(--scax-color-accent-08)" : "transparent",
              }}
              type="button"
            >
              <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{unit.name}</span>
              <span className="tabular" style={{ flex: "none", fontSize: 12, color: "var(--scax-color-ink-assistive)" }}>
                {excluded ? countOf(unit.id) : unit.count}명
              </span>
            </button>
          );
        })}
      </div>
      <div className="meeting-scroll" style={{ maxHeight: 216, overflowY: "auto", padding: 8 }}>
        {members.map((person) => {
          const on = selected.has(person.member_id);
          return (
            <div
              key={person.member_id}
              onClick={() => onToggle(person)}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                padding: "8px 12px",
                borderRadius: "var(--scax-radius-sm)",
                cursor: "pointer",
                background: on ? "var(--scax-color-accent-08)" : "transparent",
              }}
            >
              {/* 행 전체가 누르는 자리다 — 상자는 지금 담겼는지를 그리기만 한다. */}
              {mode === "check" && <CheckboxBox checked={on} />}
              <Avatar name={person.name} size="xs" />
              <span style={{ flex: 1, minWidth: 0, fontSize: 13, fontWeight: 700 }}>{person.name}</span>
              <span className="t-meta" style={{ flex: "none", fontSize: 12 }}>
                {person.rank}
              </span>
            </div>
          );
        })}
        {members.length === 0 && emptyText && (
          <div className="t-meta" style={{ padding: 8, fontSize: 12 }}>
            {emptyText}
          </div>
        )}
      </div>
    </div>
  );
}

/** 고른 사람 태그 줄 — 이름 + 부서(또는 「사외」) + `×`. 예약·머리 편집·공유가 같이 쓴다. */
export function PickedTags({ items }: { items: Array<{ key: string; name: string; note?: string; onRemove: () => void }> }) {
  if (items.length === 0) return null;
  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        gap: 8,
        padding: 12,
        border: "1px solid var(--scax-color-line-weak)",
        borderRadius: "var(--scax-radius-sm)",
        background: "var(--scax-color-surface-alt)",
      }}
    >
      {items.map((item) => (
        <button className="meeting-tag" key={item.key} onClick={item.onRemove} type="button">
          <span style={{ fontWeight: 700 }}>{item.name}</span>
          {item.note && <span style={{ color: "var(--scax-color-ink-assistive)" }}>{item.note}</span>}
          <span aria-hidden>×</span>
          <span className="sr-only">빼기</span>
        </button>
      ))}
    </div>
  );
}
