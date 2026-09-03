import { useEffect, useState } from "react";

import { getMyOrganizationProfile } from "./api";
import { capabilityText, personName } from "./labels";
import type { OrganizationProfile } from "./viewModels";

type OrgPageProps = {
  personaId: string;
  onError: (message: string | null) => void;
};

export function OrgPage({ personaId, onError }: OrgPageProps) {
  const [profile, setProfile] = useState<OrganizationProfile | null>(null);

  useEffect(() => {
    let cancelled = false;
    void getMyOrganizationProfile()
      .then((nextProfile) => {
        if (!cancelled) setProfile(nextProfile);
      })
      .catch((error: unknown) => {
        if (!cancelled) onError(error instanceof Error ? error.message : "조직 정보를 불러오지 못했습니다.");
      });
    return () => {
      cancelled = true;
    };
  }, [onError, personaId]);

  return (
    <section className="page-surface">
      <div className="page-head">
        <div>
          <h1>조직</h1>
          <p>내 소속과 현재 허용된 권한을 확인합니다. 조직도·구성원 탐색은 다음 단계에서 열립니다.</p>
        </div>
      </div>
      {profile && (
        <div className="org-grid">
          <section className="surface-card">
            <div className="card-title">
              <h2>{personName(profile.display_name)}</h2>
            </div>
            <p>현재 재직 중인 소속</p>
            <div className="org-chips">
              {profile.organizations.length === 0 ? (
                <span className="badge neutral">소속 정보 없음</span>
              ) : (
                profile.organizations.map((organization) => (
                  <span className="badge ai" key={organization.id}>
                    {organization.name}
                  </span>
                ))
              )}
            </div>
          </section>
          <section className="surface-card">
            <div className="card-title">
              <h2>허용된 권한</h2>
              <span className="badge neutral">{profile.capabilities.length}개</span>
            </div>
            <p>화면·AX 대화·MCP Tool 모두 이 권한으로 같은 업무 기능을 엽니다.</p>
            <ul className="capability-list">
              {profile.capabilities.map((capability) => (
                <li key={capability}>
                  <span>{capabilityText(capability)}</span>
                  <code>{capability}</code>
                </li>
              ))}
            </ul>
          </section>
        </div>
      )}
    </section>
  );
}
