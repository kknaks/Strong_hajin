import { Empty } from "../Empty";
import { Skeleton } from "../Skeleton";
import { membershipSummary, orgScreen, personName } from "../labels";
import type { OrganizationMember, OrganizationUnitNode } from "../viewModels";

/**
 * 패널 ② 구성원 목록 — 핸드오프 「패널 ② 구성원 목록 (1fr)」, 행 높이 68.
 *
 * 사번 자리에는 서버가 주는 구성원 key 를 그대로 둔다. 이 제품에는 아직 사번 컬럼이 없고, 없는 값을
 * 지어내는 대신 사람을 가리키는 이름표를 그 자리에 둔다.
 *
 * 행 우측의 `⋯` 메뉴는 그리지 않는다 — 그 사람에게 무엇을 할 수 있는지를 서버가 아직 말해 주지 않는다.
 */
export function MemberListPanel({
  unit,
  members,
  failed,
  selectedMemberId,
  onSelect,
}: {
  unit: OrganizationUnitNode | null;
  /** null 이면 아직 불러오는 중이다. */
  members: OrganizationMember[] | null;
  failed: boolean;
  selectedMemberId: string | null;
  onSelect: (member: OrganizationMember) => void;
}) {
  // 이 API 는 하위 조직의 사람까지 함께 준다. 세는 방식이 다르면 그렇게 말한다.
  const includesDescendants = unit ? unit.member_count !== unit.direct_member_count : false;
  const heading = unit ? `${unit.name} ${orgScreen.memberSuffix}` : orgScreen.memberSuffix;
  return (
    <section aria-label={heading} className="org-panel">
      <header
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 8,
          padding: "16px 24px",
          borderBottom: "1px solid var(--border-default)",
        }}
      >
        <h3 style={{ fontSize: 16 }}>{heading}</h3>
        {members && (
          <span className="t-meta tabular" style={{ fontSize: 12 }}>
            {includesDescendants ? "하위 조직 포함" : "직접 소속"} {members.length}명
          </span>
        )}
      </header>
      <div className="org-panel-scroll">
        {!unit ? (
          <Empty description={orgScreen.pickUnitDescription} title={orgScreen.pickUnit} />
        ) : failed ? (
          <Empty title={orgScreen.memberError} variant="error" />
        ) : members === null ? (
          // 실제 행 수만큼 자리를 잡아 둔다 — 목록이 도착해도 화면이 튀지 않는다.
          <div style={{ padding: 20 }}>
            <Skeleton label="구성원을 불러오는 중" rows={Math.max(1, Math.min(unit.member_count, 8))} />
          </div>
        ) : members.length === 0 ? (
          <Empty description={orgScreen.memberEmptyDescription} title={orgScreen.memberEmpty} />
        ) : (
          members.map((member) => {
            const selected = member.member_id === selectedMemberId;
            return (
              <button
                className={selected ? "org-member-row selected" : "org-member-row"}
                key={member.member_id}
                onClick={() => onSelect(member)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 12,
                  width: "100%",
                  height: 68,
                  padding: "0 20px",
                  borderBottom: "1px solid var(--border-subtle)",
                  textAlign: "left",
                  background: selected ? "var(--surface-selected-row)" : undefined,
                }}
                type="button"
              >
                <span className="t-meta tabular" style={{ width: 40, flex: "none", fontSize: 12 }}>
                  {member.member_id}
                </span>
                <span aria-hidden className="avatar sm">
                  {personName(member.display_name).slice(0, 1)}
                </span>
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span className="t-item" style={{ display: "block" }}>
                    {personName(member.display_name)}
                  </span>
                  <span className="t-meta" style={{ display: "block", fontSize: 12 }}>
                    {membershipSummary(member.memberships) || "—"}
                  </span>
                </span>
                <span style={{ fontSize: 13, color: "var(--text-secondary)" }}>{member.grade ?? ""}</span>
              </button>
            );
          })
        )}
      </div>
    </section>
  );
}
