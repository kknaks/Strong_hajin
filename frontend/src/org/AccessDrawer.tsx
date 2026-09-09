import { useState } from "react";

import type { OrganizationMemberAxes } from "../api";
import { Drawer } from "../Modal";
import { orgScreen, personName } from "../labels";
import { Select } from "../Select";
import type { AccessGrant, InstalledAccessRole, OrganizationUnitNode } from "../viewModels";
import { grantText } from "./MemberAxesPanel";

const copy = orgScreen.accessDrawer;

/**
 * 권한 변경 — 편집은 Drawer(840) 에서 한다 (DS 규약).
 *
 * 부여도 회수도 사유와 함께 기록된다. 그래서 사유가 비면 둘 다 막는다. 회수는 되돌리기 어려운 결정이라
 * 한 번 더 묻는데, 그 확인은 Drawer 를 닫은 뒤에 뜬다 — Drawer 위에 모달을 겹치지 않는다 (DS 규칙).
 * 보직이 데려온 권한(`origin_rule_id`)은 보직과 함께 끝나므로 여기서 따로 회수하지 않는다.
 */
export function AccessDrawer({
  member,
  grants,
  roles,
  units,
  rootUnitId,
  busy,
  onGrant,
  onRequestRevoke,
  onClose,
}: {
  member: OrganizationMemberAxes;
  grants: AccessGrant[];
  roles: InstalledAccessRole[];
  units: OrganizationUnitNode[];
  rootUnitId: string | null;
  busy: boolean;
  onGrant: (input: { roleId: string; scopeRef: string; reason: string }) => void;
  onRequestRevoke: (grant: AccessGrant, reason: string) => void;
  onClose: () => void;
}) {
  const [roleId, setRoleId] = useState("");
  const [scopeRef, setScopeRef] = useState("");
  const [reason, setReason] = useState("");
  const trimmed = reason.trim();
  const grantBlocked = !roleId || !scopeRef || !trimmed || busy;

  return (
    <Drawer
      footer={
        <>
          {!trimmed && (
            <span className="t-meta" style={{ fontSize: 13 }}>
              {copy.reasonRequired}
            </span>
          )}
          <span aria-hidden className="spacer" />
          <button className="btn ghost h40" onClick={onClose} type="button">
            {copy.cancel}
          </button>
          <button
            className="btn primary h40"
            disabled={grantBlocked}
            onClick={() => onGrant({ roleId, scopeRef, reason: trimmed })}
            type="button"
          >
            {copy.grant}
          </button>
        </>
      }
      kicker={`${personName(member.display_name)} · ${member.member_id}${member.grade ? ` · ${member.grade.name}` : ""}`}
      label={copy.label}
      onClose={onClose}
      title={copy.label}
    >
      <section aria-label={copy.granted}>
        <div style={{ marginBottom: 8, fontSize: 13, fontWeight: 700, color: "var(--text-tertiary)" }}>{copy.granted}</div>
        {grants.length === 0 ? (
          <p className="t-meta">{orgScreen.accessEmpty}</p>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {grants.map((grant) => (
              <div
                data-grant={grant.grant_id}
                key={grant.grant_id}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 12,
                  padding: "8px 12px",
                  border: "1px solid var(--border-default)",
                  borderRadius: "var(--radius-control)",
                }}
              >
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span className="t-item" style={{ display: "block" }}>
                    {grantText(grant)}
                  </span>
                  <span className="t-meta" style={{ display: "block", fontSize: 12 }}>
                    {grant.origin_rule_id ? copy.standardGrant : copy.directGrant}
                  </span>
                </span>
                {!grant.origin_rule_id && (
                  <button
                    className="btn ghost h30"
                    disabled={busy || !trimmed}
                    onClick={() => onRequestRevoke(grant, trimmed)}
                    type="button"
                  >
                    {copy.revoke}
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
      </section>

      <div className="form-stack" style={{ marginTop: 24 }}>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 16 }}>
          <div className="field">
            <span>{copy.role}</span>
            <Select
              id="access-role"
              label={copy.role}
              onChange={setRoleId}
              options={roles.map((role) => ({ value: role.role_id, label: `${role.label}${role.customized ? " (수정됨)" : ""}` }))}
              placeholder={copy.rolePlaceholder}
              value={roleId}
            />
          </div>
          <div className="field">
            <span>{copy.scope}</span>
            <Select
              id="access-scope"
              label={copy.scope}
              onChange={setScopeRef}
              options={units.map((unit) => ({ value: unit.id, label: `${unit.name}${unit.id === rootUnitId ? " (조직 전체)" : ""}` }))}
              placeholder={copy.scopePlaceholder}
              value={scopeRef}
            />
          </div>
        </div>
        <div className="field">
          <label htmlFor="access-reason">{copy.reason}</label>
          <textarea
            id="access-reason"
            onChange={(event) => setReason(event.target.value)}
            placeholder="왜 바꾸는지 적어주세요"
            value={reason}
          />
          <span className="field-help">{copy.reasonHelp}</span>
        </div>
        <p className="t-meta">{copy.lastAdminNote}</p>
      </div>
    </Drawer>
  );
}
