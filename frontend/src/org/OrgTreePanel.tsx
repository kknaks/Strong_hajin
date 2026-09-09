import { Empty } from "../Empty";
import { Icon } from "../Icon";
import { Skeleton } from "../Skeleton";
import { orgScreen, personName } from "../labels";
import type { OrganizationUnitNode } from "../viewModels";

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
      <div key={unit.id} style={depth > 0 ? { paddingLeft: 16 } : undefined}>
        <div
          className={selected ? "org-row selected" : "org-row"}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 9,
            padding: "8px 12px",
            borderRadius: "var(--radius-control)",
            background: selected ? "var(--selected-bg)" : undefined,
          }}
        >
          {children.length > 0 ? (
            <button
              aria-expanded={expanded}
              aria-label={`${unit.name} ${expanded ? "접기" : "펼치기"}`}
              onClick={() => onToggle(unit.id)}
              style={{ display: "flex", flex: "none", color: "var(--text-tertiary)" }}
              type="button"
            >
              <Icon name={expanded ? "chevron-down" : "chevron-right"} size={12} />
            </button>
          ) : (
            <span aria-hidden style={{ width: 12, flex: "none" }} />
          )}
          <button
            onClick={() => onSelect(unit.id)}
            style={{ flex: 1, minWidth: 0, textAlign: "left" }}
            type="button"
          >
            <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span className="t-item" style={selected ? { color: "var(--action)" } : undefined}>
                {unit.name}
              </span>
              {unit.unit_type && <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>{unit.unit_type}</span>}
            </span>
            <span className="t-meta tabular" style={{ display: "block", fontSize: 12 }}>
              직접 {unit.direct_member_count} · 전체 {unit.member_count}
            </span>
          </button>
          {leader && (
            <span aria-hidden className="avatar xs" title={`${personName(leader.display_name)} ${leader.position}`}>
              {personName(leader.display_name).slice(0, 1)}
            </span>
          )}
        </div>
        {expanded && children.length > 0 && children.map((child) => renderUnit(child, depth + 1))}
      </div>
    );
  };

  const roots = (childrenOf.get(null) ?? []).filter((unit) => !visibleUnitIds || visibleUnitIds.has(unit.id));
  return (
    <section aria-label={orgScreen.treeTitle} className="org-panel">
      <header
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 12,
          padding: "16px 24px",
          borderBottom: "1px solid var(--border-default)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
          <h3 style={{ fontSize: 16 }}>{orgScreen.treeTitle}</h3>
          <span className="t-meta" style={{ fontSize: 12 }}>
            {orgScreen.treeMeta}
          </span>
        </div>
        <input
          aria-label={orgScreen.searchPlaceholder}
          className="search-input-box"
          onChange={(event) => onQueryChange(event.target.value)}
          placeholder={orgScreen.searchPlaceholder}
          type="text"
          value={query}
        />
      </header>
      <div className="org-panel-scroll" style={{ padding: "8px 16px 16px" }}>
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
