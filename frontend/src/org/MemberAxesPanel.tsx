import { useState, type ReactNode } from "react";

import type { MemberHistoryAxis, OrganizationMemberAxes } from "../api";
import { Empty, EmptyValue } from "../Empty";
import { Skeleton } from "../Skeleton";
import { employmentStateText, formatPeriod, orgScreen, personName } from "../labels";
import type { AccessGrant } from "../viewModels";
import { AxisHistoryButton } from "./AxisHistoryPopover";

/** 상세를 부르는 동안·부르고 나서. */
export type DetailView =
  | { status: "empty" }
  | { status: "loading" }
  | { status: "ready"; detail: OrganizationMemberAxes }
  | { status: "error" };

export function grantText(grant: AccessGrant): string {
  const role = grant.role_label ?? grant.capability_id ?? grant.role_id ?? "역할";
  const scope = grant.scope_name ?? grant.scope_ref ?? "전체";
  return `${role} · ${scope}${grant.include_descendants ? " 이하" : ""}`;
}

/** 소속 한 줄 — 「제품기획팀 (주) · 디자인팀 (겸직)」. */
function affiliationText(memberships: OrganizationMemberAxes["memberships"]): string {
  return memberships.map((item) => `${item.unit_name} (${item.kind === "primary" ? "주" : "겸직"})`).join(" · ");
}

/** 처음에 보여 주는 회수 이력의 수. 그보다 많으면 접어 두고 「n건 더 보기」로 편다. */
const REVOKED_PREVIEW = 3;

/** 한 축의 한 줄 — 라벨 36 · 값 14 + 보조설명 12 · 오른쪽 액션. */
function AxisRow({ label, value, caption, history, actions }: { label: string; value: ReactNode; caption: string; history: ReactNode; actions?: ReactNode }) {
  return (
    <div style={{ display: "flex", alignItems: "flex-start", gap: 12, padding: "8px 0", borderBottom: "1px solid var(--border-subtle)" }}>
      <span style={{ width: 36, flex: "none", fontSize: 13, fontWeight: 700, color: "var(--text-tertiary)" }}>{label}</span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 14 }}>{value}</div>
        <div className="t-meta" style={{ fontSize: 12 }}>
          {caption}
        </div>
      </div>
      {history}
      {actions}
    </div>
  );
}

/**
 * 패널 ③ 여섯 축 상세 — 핸드오프 「패널 ③ 6축 상세 (600)」.
 *
 * 한 사람은 `GET /api/organization/members/{id}` **한 번**으로 읽는다. 여섯 축·재직·계정 유무·권한·회수된
 * 권한이 한 응답에 함께 오고, 볼 자격이 없는 축은 자리를 남기고 값만 비어 온다.
 *
 * 「변경」은 여전히 권한 축에만 있다 — 소속·직책을 바꾸는 command 가 서버에 아직 없다 (SPEC-005 §5).
 * 「이력」은 축마다 그 축의 이력 API 를 부른다. 계층만 부를 곳이 없어 눌리지 않는다.
 */
export function MemberAxesPanel({
  view,
  canReadSensitive,
  canManageAccess,
  onChangeAccess,
  onHistoryDenied,
}: {
  view: DetailView;
  /** 본인이거나 그 사람을 관리할 수 있는 사람 — 서버가 권한·이력을 여는 기준과 같다. */
  canReadSensitive: boolean;
  canManageAccess: boolean;
  onChangeAccess: () => void;
  onHistoryDenied: (message: string) => void;
}) {
  const [revokedExpanded, setRevokedExpanded] = useState(false);

  if (view.status === "empty" || view.status === "loading" || view.status === "error") {
    return (
      <section aria-label="구성원 상세" className="org-panel">
        <div className="org-panel-scroll">
          {view.status === "empty" ? (
            <Empty description={orgScreen.pickMemberDescription} title={orgScreen.pickMember} />
          ) : view.status === "error" ? (
            <Empty title={orgScreen.memberError} variant="error" />
          ) : (
            <div style={{ padding: 24 }}>
              <Skeleton label="구성원을 불러오는 중" rows={6} />
            </div>
          )}
        </div>
      </section>
    );
  }

  const detail = view.detail;
  const name = personName(detail.display_name);
  const axes = orgScreen.axes;
  const revoked = canReadSensitive ? detail.revoked_grants : [];
  const shownRevoked = revokedExpanded ? revoked : revoked.slice(0, REVOKED_PREVIEW);
  const history = (axis: MemberHistoryAxis | null, label: string) =>
    canReadSensitive ? <AxisHistoryButton axis={axis} memberId={detail.member_id} onDenied={onHistoryDenied} title={`${label} ${orgScreen.history}`} /> : null;

  return (
    <section aria-label={`${name} 상세`} className="org-panel">
      <header style={{ display: "flex", alignItems: "center", gap: 12, padding: "12px 24px", borderBottom: "1px solid var(--border-default)" }}>
        <span aria-hidden className="avatar lg">
          {name.slice(0, 1)}
        </span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <h3 style={{ fontSize: 20 }}>{name}</h3>
            <span className="t-meta tabular" style={{ fontSize: 12 }}>
              {detail.member_id}
            </span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 4, marginTop: 4 }}>
            <span className="badge neutral">{employmentStateText(detail.employment_state)}</span>
            {/* 계정이 있다는 것은 권한이 아니라 들어올 문이 있다는 뜻이다. */}
            <span className="badge neutral">{detail.has_account ? orgScreen.hasAccount : orgScreen.noAccount}</span>
            <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>{orgScreen.employmentCaption}</span>
          </div>
        </div>
      </header>
      <div className="org-panel-scroll" style={{ padding: "8px 24px 16px" }}>
        <AxisRow
          caption={axes.hierarchy.caption}
          history={history(null, axes.hierarchy.label)}
          label={axes.hierarchy.label}
          value={detail.hierarchy_path.length > 0 ? detail.hierarchy_path.map((unit) => unit.name).join(" › ") : <EmptyValue />}
        />
        <AxisRow
          caption={axes.affiliation.caption}
          history={history("membership", axes.affiliation.label)}
          label={axes.affiliation.label}
          value={detail.memberships.length > 0 ? affiliationText(detail.memberships) : <EmptyValue />}
        />
        <AxisRow
          caption={axes.appointment.caption}
          history={history("appointment", axes.appointment.label)}
          label={axes.appointment.label}
          value={
            detail.appointments.length > 0 ? detail.appointments.map((item) => `${item.unit_name} ${item.position}`).join(" · ") : <EmptyValue />
          }
        />
        <AxisRow
          caption={axes.grade.caption}
          history={history("grade", axes.grade.label)}
          label={axes.grade.label}
          value={detail.grade?.name ?? <EmptyValue />}
        />
        <AxisRow
          caption={axes.job.caption}
          history={history("job", axes.job.label)}
          label={axes.job.label}
          value={detail.jobs.length > 0 ? detail.jobs.map((job) => job.name).join(" · ") : <EmptyValue />}
        />

        <div style={{ marginTop: 12, padding: 16, borderRadius: "var(--radius-control)", background: "var(--selected-bg)" }}>
          <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
            <span style={{ width: 36, flex: "none", fontSize: 13, fontWeight: 700 }}>{axes.access.label}</span>
            <div style={{ flex: 1, minWidth: 0 }}>
              {canReadSensitive ? (
                <>
                  <div className="t-item">{detail.grants.length > 0 ? detail.grants.map(grantText).join(" / ") : orgScreen.accessEmpty}</div>
                  <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>{axes.access.caption}</div>
                </>
              ) : (
                <>
                  <div className="t-item">
                    <EmptyValue />
                  </div>
                  <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>{orgScreen.accessRestricted}</div>
                </>
              )}
            </div>
            {history("grant", axes.access.label)}
            {canManageAccess && (
              <button className="btn primary h30" onClick={onChangeAccess} type="button">
                {orgScreen.change}
              </button>
            )}
          </div>

          {/* 회수는 지우는 것이 아니다 — 지금 닿지 않는다는 사실과 언제 거두었는지가 함께 남는다. */}
          {revoked.length > 0 && (
            <div style={{ marginTop: 8, paddingTop: 8, borderTop: "1px solid var(--ai-border)" }}>
              <div style={{ marginBottom: 4, fontSize: 11, fontWeight: 700, color: "var(--text-tertiary)" }}>{orgScreen.revokedTitle}</div>
              <div style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12, color: "var(--text-secondary)" }}>
                {shownRevoked.map((item) => (
                  <div key={item.grant_id} style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                    <span>
                      {item.role_label ?? item.role_id ?? "역할"} · {item.scope_name ?? item.scope_ref ?? "전체"}
                    </span>
                    <span className="tabular" style={{ flex: "none", color: "var(--text-tertiary)" }}>
                      {formatPeriod(item.valid_from, item.revoked_at)}
                    </span>
                  </div>
                ))}
              </div>
              {revoked.length > REVOKED_PREVIEW && (
                <button className="btn link h30" onClick={() => setRevokedExpanded((value) => !value)} style={{ paddingLeft: 0 }} type="button">
                  {revokedExpanded ? orgScreen.revokedLess : orgScreen.revokedMore(revoked.length - REVOKED_PREVIEW)}
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
