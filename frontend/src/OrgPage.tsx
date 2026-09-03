import { useEffect, useState } from "react";

import { getMyOrganizationProfile } from "./api";
import type { OrganizationProfile } from "./viewModels";

type OrgPageProps = {
  personaId: string;
  onError: (message: string | null) => void;
};

export function OrgPage({ personaId, onError }: OrgPageProps) {
  const [profile, setProfile] = useState<OrganizationProfile | null>(null);

  useEffect(() => {
    let cancelled = false;
    void getMyOrganizationProfile(personaId)
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
      <p className="kicker">ORGANIZATION</p>
      <h2>조직</h2>
      <p>Organization &amp; Access 원장의 현재 권한 범위를 표시합니다.</p>
      {profile && (
        <section className="surface-card organization-seam">
          <h3>{profile.display_name}의 접근 범위</h3>
          <p>{profile.organizations.map((organization) => organization.name).join(" · ")}</p>
          <p className="report-notice">{profile.capabilities.join(" · ")}</p>
        </section>
      )}
    </section>
  );
}
