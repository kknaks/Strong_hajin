import { useState } from "react";

import { Button } from "../../ds/Button";
import type { OrganizationMemberAxes } from "../../lib/api";
import { Drawer } from "../../ds/Modal";
import { emptyActionLabel, orgScreen, personName, selectLabel } from "../../lib/labels";
import { Select } from "../../ds/Select";
import type { AccessGrant, InstalledAccessRole, OrganizationUnitNode } from "../../lib/viewModels";
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
          closeLabel="상세 닫기"
      footer={
        <>
          {!trimmed && (
            <span className="org-drawer__note">{copy.reasonRequired}</span>
          )}
          <span aria-hidden className="scax-drawer__spacer" />
          <Button variant="text" onClick={onClose} type="button">
            {copy.cancel}
          </Button>
          <Button variant="solid" tone="primary" disabled={grantBlocked} onClick={() => onGrant({ roleId, scopeRef, reason: trimmed })}
            type="button"
          >
            {copy.grant}
          </Button>
        </>
      }
      kicker={`${personName(member.display_name)} · ${member.member_id}${member.grade ? ` · ${member.grade.name}` : ""}`}
      label={copy.label}
      onClose={onClose}
      title={copy.label}
    >
      <section aria-label={copy.granted}>
        <div className="org-drawer__section-title">{copy.granted}</div>
        {grants.length === 0 ? (
          <p className="org-drawer__note">{orgScreen.accessEmpty}</p>
        ) : (
          <div className="org-grant-list">
            {grants.map((grant) => (
              <div className="org-grant-row" data-grant={grant.grant_id} key={grant.grant_id}>
                <span className="org-grant-row__body">
                  <span className="org-grant-row__title">{grantText(grant)}</span>
                  <span className="org-grant-row__origin">{grant.origin_rule_id ? copy.standardGrant : copy.directGrant}</span>
                </span>
                {!grant.origin_rule_id && (
                  <Button variant="text" size="sm" disabled={busy || !trimmed} onClick={() => onRequestRevoke(grant, trimmed)}
                    type="button"
                  >
                    {copy.revoke}
                  </Button>
                )}
              </div>
            ))}
          </div>
        )}
      </section>

      <div className="form-stack org-drawer__form">
        <div className="org-drawer__pair">
          <div className="scax-field">
            <span>{copy.role}</span>
            <Select
            emptyActionLabel={emptyActionLabel.filter}
            labels={selectLabel}
              id="access-role"
              label={copy.role}
              onChange={setRoleId}
              options={roles.map((role) => ({ value: role.role_id, label: `${role.label}${role.customized ? " (수정됨)" : ""}` }))}
              placeholder={copy.rolePlaceholder}
              value={roleId}
            />
          </div>
          <div className="scax-field">
            <span>{copy.scope}</span>
            <Select
            emptyActionLabel={emptyActionLabel.filter}
            labels={selectLabel}
              id="access-scope"
              label={copy.scope}
              onChange={setScopeRef}
              options={units.map((unit) => ({ value: unit.id, label: `${unit.name}${unit.id === rootUnitId ? " (조직 전체)" : ""}` }))}
              placeholder={copy.scopePlaceholder}
              value={scopeRef}
            />
          </div>
        </div>
        <div className="scax-field">
          <label className="scax-field__label" htmlFor="access-reason">{copy.reason}</label>
          <textarea
            className="scax-textarea"
            id="access-reason"
            onChange={(event) => setReason(event.target.value)}
            placeholder="왜 바꾸는지 적어주세요"
            value={reason}
          />
          <span className="scax-field__hint">{copy.reasonHelp}</span>
        </div>
        <p className="org-drawer__note">{copy.lastAdminNote}</p>
      </div>
    </Drawer>
  );
}
