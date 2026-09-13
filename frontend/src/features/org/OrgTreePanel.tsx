import { Empty } from "../../ds/Empty";
import { Avatar } from "../../ds/Avatar";
import { Icon } from "../../ds/icons/Icon";
import { Skeleton } from "../../ds/Skeleton";
import { orgScreen, personName } from "../../lib/labels";
import type { OrganizationUnitNode } from "../../lib/viewModels";

/**
 * 패널 ① 조직 tree — 핸드오프 「패널 ① 조직 tree (392)」.
 *
 * 펼침과 선택은 서로 다른 일이다. chevron 은 접기/펼치기만 하고, 이름을 누르면 그 조직이 선택된다 —
 * 접힌 조직도 선택할 수 있고, 선택한 조직을 접을 수도 있다.
 */
export function OrgTreePanel({
  childrenOf,
  selectedUnitId,
  expandedUnitIds,
  visibleUnitIds,
  query,
  loading,
  failed,
  onQueryChange,
  onToggle,
  onSelect,
}: {
  childrenOf: Map<string | null, OrganizationUnitNode[]>;
  selectedUnitId: string | null;
  expandedUnitIds: Set<string>;
  /** 검색 중일 때만 값이 있다. 그때는 이 집합에 든 조직만 그리고, 전부 펼친 것으로 본다. */
  visibleUnitIds: Set<string> | null;
  query: string;
  loading: boolean;
  failed: boolean;
  onQueryChange: (value: string) => void;
  onToggle: (unitId: string) => void;
  onSelect: (unitId: string) => void;
}) {
  const renderUnit = (unit: OrganizationUnitNode, depth: number) => {
    const children = (childrenOf.get(unit.id) ?? []).filter((child) => !visibleUnitIds || visibleUnitIds.has(child.id));
    const expanded = visibleUnitIds ? true : expandedUnitIds.has(unit.id);
    const selected = unit.id === selectedUnitId;
    const leader = unit.leaders[0];
    return (
      <div className={depth > 0 ? "org-tree-child" : undefined} key={unit.id}>
        <div className={selected ? "org-row selected" : "org-row"}>
          {children.length > 0 ? (
            <button
              aria-expanded={expanded}
              aria-label={`${unit.name} ${expanded ? "접기" : "펼치기"}`}
              className="org-row__caret"
              onClick={() => onToggle(unit.id)}
              type="button"
            >
              <Icon name={expanded ? "chevron-down" : "chevron-right"} size={12} />
            </button>
          ) : (
            <span aria-hidden className="org-row__caret-blank" />
          )}
          <button className="org-row__main" onClick={() => onSelect(unit.id)} type="button">
            <span className="org-row__line">
              <span className="org-row__name">{unit.name}</span>
              {unit.unit_type && <span className="org-row__kind">{unit.unit_type}</span>}
            </span>
            <span className="org-row__count">
              직접 {unit.direct_member_count} · 전체 {unit.member_count}
            </span>
          </button>
          {leader && (
            <Avatar name={personName(leader.display_name)} size="xs" title={`${personName(leader.display_name)} ${leader.position}`} />
          )}
        </div>
        {expanded && children.length > 0 && children.map((child) => renderUnit(child, depth + 1))}
      </div>
    );
  };

  const roots = (childrenOf.get(null) ?? []).filter((unit) => !visibleUnitIds || visibleUnitIds.has(unit.id));
  return (
    <section aria-label={orgScreen.treeTitle} className="org-panel">
      <header className="org-panel__head">
        <div className="org-panel__head-top">
          <h3 className="org-panel__title">{orgScreen.treeTitle}</h3>
          <span className="org-panel__meta">{orgScreen.treeMeta}</span>
        </div>
        {/* 바퀴 8-B: 맨 클래스 `.search-input-box` 이던 검색칸을 새 DS 의 텍스트 필드 골격
            (`.scax-textfield` + `__input`)으로 옮겼다. 아직 부품(TextField)이 `src/` 에 없어
            클래스로 직접 쓴다 — 부품화는 이 바퀴의 몫이 아니다. */}
        <div className="scax-textfield">
          <input
            aria-label={orgScreen.searchPlaceholder}
            className="scax-textfield__input"
            onChange={(event) => onQueryChange(event.target.value)}
            placeholder={orgScreen.searchPlaceholder}
            type="text"
            value={query}
          />
        </div>
      </header>
      <div className="org-panel-scroll org-tree-scroll">
        {loading ? (
          <Skeleton label="조직을 불러오는 중" rows={6} />
        ) : failed ? (
          <Empty title={orgScreen.treeError} variant="error" />
        ) : roots.length === 0 ? (
          <Empty title={orgScreen.searchEmpty} variant="filter" />
        ) : (
          roots.map((unit) => renderUnit(unit, 0))
        )}
      </div>
    </section>
  );
}
