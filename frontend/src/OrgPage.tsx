import { useCallback, useEffect, useMemo, useState } from "react";

import {
  getInstalledAccessRoles,
  getMemberAccess,
  getMyOrganizationProfile,
  getOrganizationTree,
  getOrganizationUnitMembers,
  grantAccessRole,
  revokeAccessGrant,
} from "./api";
import { capabilityText, personName } from "./labels";
import type {
  InstalledAccessRole,
  MemberAccess,
  OrganizationMember,
  OrganizationProfile,
  OrganizationUnitNode,
} from "./viewModels";

type OrgPageProps = {
  personaId: string;
  onError: (message: string | null) => void;
};

export function OrgPage({ personaId, onError }: OrgPageProps) {
  const [profile, setProfile] = useState<OrganizationProfile | null>(null);
  const [units, setUnits] = useState<OrganizationUnitNode[]>([]);
  const [selectedUnit, setSelectedUnit] = useState<string | null>(null);
  const [members, setMembers] = useState<OrganizationMember[] | null>(null);
  const [selectedMember, setSelectedMember] = useState<OrganizationMember | null>(null);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [roles, setRoles] = useState<InstalledAccessRole[] | null>(null);
  const [access, setAccess] = useState<MemberAccess | null>(null);
  const [grantRole, setGrantRole] = useState("");
  const [grantScope, setGrantScope] = useState("");
  const [grantReason, setGrantReason] = useState("");
  const [busy, setBusy] = useState(false);
  const administers = (profile?.capabilities ?? []).includes("organization.manage");

  useEffect(() => {
    let cancelled = false;
    void Promise.all([getMyOrganizationProfile(), getOrganizationTree()])
      .then(([nextProfile, tree]) => {
        if (cancelled) return;
        setProfile(nextProfile);
        setUnits(tree);
        setSelectedUnit((current) => current ?? nextProfile.organizations.find((item) => item.id !== "scax")?.id ?? tree[0]?.id ?? null);
        onError(null);
      })
      .catch((error: unknown) => {
        if (!cancelled) onError(error instanceof Error ? error.message : "조직 정보를 불러오지 못했습니다.");
      });
    return () => {
      cancelled = true;
    };
  }, [onError, personaId]);

  useEffect(() => {
    if (!selectedUnit) return;
    let cancelled = false;
    setMembers(null);
    setSelectedMember(null);
    void getOrganizationUnitMembers(selectedUnit)
      .then((items) => {
        if (!cancelled) setMembers(items);
      })
      .catch((error: unknown) => {
        if (!cancelled) onError(error instanceof Error ? error.message : "구성원을 불러오지 못했습니다.");
      });
    return () => {
      cancelled = true;
    };
  }, [onError, selectedUnit]);

  useEffect(() => {
    if (!administers) return;
    let cancelled = false;
    void getInstalledAccessRoles()
      .then((items) => {
        if (!cancelled) setRoles(items);
      })
      .catch(() => {
        if (!cancelled) setRoles([]);
      });
    return () => {
      cancelled = true;
    };
  }, [administers]);

  const loadAccess = useCallback(
    async (memberId: string | null) => {
      if (!administers || !memberId) {
        setAccess(null);
        return;
      }
      setAccess(await getMemberAccess(memberId).catch(() => null));
    },
    [administers],
  );

  useEffect(() => {
    void loadAccess(selectedMember?.member_id ?? null);
  }, [loadAccess, selectedMember]);

  async function grant() {
    if (!selectedMember || !grantRole || !grantScope || !grantReason.trim() || busy) return;
    setBusy(true);
    onError(null);
    try {
      await grantAccessRole({
        member_id: selectedMember.member_id,
        role_id: grantRole,
        scope_kind: grantScope === "scax" ? "organization" : "unit",
        scope_ref: grantScope,
        include_descendants: true,
        reason: grantReason.trim(),
      });
      setGrantReason("");
      await loadAccess(selectedMember.member_id);
    } catch (error) {
      onError(error instanceof Error ? error.message : "권한을 부여하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  async function revoke(grantId: string) {
    if (!selectedMember || busy) return;
    const reason = grantReason.trim() || "권한 회수";
    setBusy(true);
    onError(null);
    try {
      await revokeAccessGrant(grantId, reason);
      await loadAccess(selectedMember.member_id);
    } catch (error) {
      onError(error instanceof Error ? error.message : "권한을 회수하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  const childrenOf = useMemo(() => {
    const map = new Map<string | null, OrganizationUnitNode[]>();
    for (const unit of units) {
      const list = map.get(unit.parent_id) ?? [];
      list.push(unit);
      map.set(unit.parent_id, list);
    }
    for (const list of map.values()) list.sort((a, b) => a.display_order - b.display_order || a.id.localeCompare(b.id));
    return map;
  }, [units]);
  const unitById = useMemo(() => new Map(units.map((unit) => [unit.id, unit])), [units]);
  const selected = selectedUnit ? unitById.get(selectedUnit) : undefined;

  const renderUnit = (unit: OrganizationUnitNode, depth: number) => {
    const children = childrenOf.get(unit.id) ?? [];
    const isCollapsed = collapsed.has(unit.id);
    return (
      <li key={unit.id}>
        <div className={unit.id === selectedUnit ? "org-node selected" : "org-node"} style={{ paddingLeft: 12 + depth * 20 }}>
          {children.length > 0 ? (
            <button
              aria-expanded={!isCollapsed}
              aria-label={isCollapsed ? `${unit.name} 펼치기` : `${unit.name} 접기`}
              className="org-caret"
              onClick={() =>
                setCollapsed((current) => {
                  const next = new Set(current);
                  if (next.has(unit.id)) next.delete(unit.id);
                  else next.add(unit.id);
                  return next;
                })
              }
              type="button"
            >
              {isCollapsed ? "▸" : "▾"}
            </button>
          ) : (
            <span className="org-caret placeholder">–</span>
          )}
          <button className="org-node-main" onClick={() => setSelectedUnit(unit.id)} type="button">
            <b>{unit.name}</b>
            {unit.unit_type && <span className="badge outline">{unit.unit_type}</span>}
            {unit.leaders.map((leader) => (
              <span className="badge neutral" key={`${leader.display_name}-${leader.position}`}>
                {personName(leader.display_name)} {leader.position}
                {leader.kind === "acting" ? " 직무대행" : ""}
              </span>
            ))}
            <span className="t-meta">전체 {unit.member_count}명</span>
          </button>
        </div>
        {!isCollapsed && children.length > 0 && <ul>{children.map((child) => renderUnit(child, depth + 1))}</ul>}
        {isCollapsed && children.length > 0 && (
          <div className="t-meta" style={{ paddingLeft: 44 + depth * 20 }}>
            하위 {children.length}
          </div>
        )}
      </li>
    );
  };

  return (
    <section className="page-surface">
      <div className="page-head">
        <div>
          <h1>조직과 구성원</h1>
          <p>조직도는 탐색 기준일 뿐 업무·인사·권한 열람 범위를 넓히지 않습니다. 상위 조직을 고르면 하위 조직의 재직 구성원까지 함께 봅니다.</p>
        </div>
      </div>

      <div className="org-layout">
        <div>
          <h2 className="section-title">조직도</h2>
          <div className="decision-panel">
            <ul className="org-tree">{(childrenOf.get(null) ?? []).map((unit) => renderUnit(unit, 0))}</ul>
          </div>

          <h2 className="section-title">
            {selected?.name ?? "조직"} <small>전체 {selected?.member_count ?? 0}명</small>
          </h2>
          <div className="decision-panel">
            {members === null ? (
              <p className="t-meta">불러오는 중…</p>
            ) : members.length === 0 ? (
              <div className="empty-state">
                <b>재직 중인 구성원이 없습니다</b>
                <p>하위 조직을 포함해도 현재 소속된 사람이 없습니다.</p>
              </div>
            ) : (
              <ul className="member-list">
                {members.map((member) => (
                  <li key={member.member_id}>
                    <button
                      className={selectedMember?.member_id === member.member_id ? "member-row selected" : "member-row"}
                      onClick={() => setSelectedMember(member)}
                      type="button"
                    >
                      <span className="avatar md" aria-hidden>
                        {personName(member.display_name).slice(0, 1)}
                      </span>
                      <span className="member-main">
                        <b>{personName(member.display_name)}</b>
                        <span className="chip-row" style={{ marginTop: 4 }}>
                          {member.memberships.map((membership) => (
                            <span className="badge ai" key={membership.organization_id}>
                              {membership.organization_name} · {membership.kind === "primary" ? "주소속" : "겸직"}
                            </span>
                          ))}
                          {member.positions.map((position) => (
                            <span className="badge neutral" key={`${position.organization_name}-${position.position}`}>
                              {position.organization_name} {position.position}
                            </span>
                          ))}
                        </span>
                      </span>
                      <span className="t-meta">{[member.grade, ...member.jobs].filter(Boolean).join(" · ") || "—"}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>

        <aside>
          <h2 className="section-title">구성원 상세</h2>
          {selectedMember ? (
            <div className="decision-panel">
              <div className="member-head">
                <span className="avatar lg" aria-hidden>
                  {personName(selectedMember.display_name).slice(0, 1)}
                </span>
                <div>
                  <b className="t-item">{personName(selectedMember.display_name)}</b>
                  <p className="t-meta">{[selectedMember.grade, ...selectedMember.jobs].filter(Boolean).join(" · ") || "직급·직무 정보 없음"}</p>
                </div>
              </div>
              <dl className="meta-grid" style={{ marginTop: 16 }}>
                <div>
                  <dt>소속</dt>
                  <dd>{selectedMember.memberships.map((item) => `${item.organization_name}(${item.kind === "primary" ? "주소속" : "겸직"})`).join(", ") || "—"}</dd>
                </div>
                <div>
                  <dt>보직</dt>
                  <dd>{selectedMember.positions.map((item) => `${item.organization_name} ${item.position}`).join(", ") || "없음"}</dd>
                </div>
                <div>
                  <dt>직급</dt>
                  <dd>{selectedMember.grade ?? "—"}</dd>
                </div>
                <div>
                  <dt>직무</dt>
                  <dd>{selectedMember.jobs.join(", ") || "—"}</dd>
                </div>
              </dl>
              {administers ? (
                <section aria-label="구성원 권한" className="member-access">
                  <h3 className="t-item">권한</h3>
                  {access === null ? (
                    <p className="t-meta">권한을 불러오는 중…</p>
                  ) : (
                    <>
                      <ul className="grant-list" aria-label="부여된 권한">
                        {access.grants.length === 0 && <li className="t-meta">부여된 권한이 없습니다.</li>}
                        {access.grants.map((item) => (
                          <li data-grant={item.grant_id} key={item.grant_id}>
                            <b>{item.role_label ?? item.capability_id ?? item.role_id}</b>
                            <span className="t-meta">
                              {item.scope_name ?? item.scope_ref ?? "전체"}
                              {item.include_descendants ? " 이하" : ""} · {item.origin_rule_id ? "보직 표준 부여" : "직접 부여"}
                            </span>
                            {!item.origin_rule_id && (
                              <button className="btn h30" disabled={busy} onClick={() => void revoke(item.grant_id)} type="button">
                                회수
                              </button>
                            )}
                          </li>
                        ))}
                      </ul>
                      <div className="grant-form">
                        <label className="field" htmlFor="grant-role">
                          <span>역할</span>
                          <select id="grant-role" onChange={(event) => setGrantRole(event.target.value)} value={grantRole}>
                            <option value="">역할 선택</option>
                            {(roles ?? []).map((role) => (
                              <option key={role.role_id} value={role.role_id}>
                                {role.label}
                                {role.customized ? " (수정됨)" : ""}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label className="field" htmlFor="grant-scope">
                          <span>범위</span>
                          <select id="grant-scope" onChange={(event) => setGrantScope(event.target.value)} value={grantScope}>
                            <option value="">범위 선택</option>
                            {units.map((unit) => (
                              <option key={unit.id} value={unit.id}>
                                {unit.name}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label className="field" htmlFor="grant-reason">
                          <span>사유</span>
                          <input id="grant-reason" onChange={(event) => setGrantReason(event.target.value)} value={grantReason} />
                        </label>
                        <button
                          className="btn h30 primary"
                          disabled={busy || !grantRole || !grantScope || !grantReason.trim()}
                          onClick={() => void grant()}
                          type="button"
                        >
                          권한 부여
                        </button>
                      </div>
                      <p className="t-meta">부여와 회수는 사유와 함께 기록되고, 조직을 관리할 사람이 아무도 남지 않는 회수는 거절됩니다.</p>
                    </>
                  )}
                </section>
              ) : (
                <p className="t-meta" style={{ marginTop: 12 }}>
                  권한·비공개 업무·인사 이력은 별도 권한이 없으면 표시하지 않습니다.
                </p>
              )}
            </div>
          ) : (
            <div className="decision-panel">
              <div className="empty-state">
                <b>구성원을 선택하세요</b>
                <p>조직도에서 조직을, 목록에서 사람을 고르면 소속·보직·직급·직무를 봅니다.</p>
              </div>
            </div>
          )}

          {profile && (
            <>
              <h2 className="section-title">
                내 권한 <small>{profile.capabilities.length}개</small>
              </h2>
              <div className="decision-panel">
                {profile.grants && profile.grants.length > 0 && (
                  <ul className="grant-list" aria-label="권한 부여">
                    {profile.grants.map((grant) => (
                      <li key={grant.grant_id}>
                        <b>{grant.role_label ?? grant.capability_id ?? grant.role_id}</b>
                        <span className="t-meta">
                          {grant.scope_name ?? grant.scope_ref ?? "전체"}
                          {grant.include_descendants ? " 이하" : ""} · {grant.origin_rule_id ? "보직 표준 부여" : "직접 부여"}
                          {grant.role_capability_version ? ` · 역할 v${grant.role_capability_version} 고정` : ""}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
                <ul className="capability-list">
                  {profile.capabilities.map((capability) => (
                    <li key={capability}>
                      <span>{capabilityText(capability)}</span>
                      <code>{capability}</code>
                    </li>
                  ))}
                </ul>
              </div>
            </>
          )}
        </aside>
      </div>
    </section>
  );
}
