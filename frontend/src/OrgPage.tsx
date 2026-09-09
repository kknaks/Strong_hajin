import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  getInstalledAccessRoles,
  getMyOrganizationProfile,
  getOrganizationActivity,
  getOrganizationMemberAxes,
  getOrganizationTree,
  getOrganizationUnitMembers,
  grantAccessRole,
  revokeAccessGrant,
  type OrganizationActivityEvent,
} from "./api";
import { ConfirmModal, Toast } from "./Modal";
import { orgScreen, personName } from "./labels";
import { AccessDrawer } from "./org/AccessDrawer";
import { ChangeLogPanel } from "./org/ChangeLogPanel";
import { MemberAxesPanel, grantText, type DetailView } from "./org/MemberAxesPanel";
import { MemberListPanel } from "./org/MemberListPanel";
import { OrgTreePanel } from "./org/OrgTreePanel";
import type {
  AccessGrant,
  InstalledAccessRole,
  OrganizationMember,
  OrganizationProfile,
  OrganizationUnitNode,
} from "./viewModels";

type OrgPageProps = {
  personaId: string;
  onError: (message: string | null) => void;
};

/** 회수를 기다리는 결정 하나. Drawer 를 닫은 뒤에 확인을 띄우기 위해 사유를 함께 들고 있는다. */
type PendingRevoke = { grant: AccessGrant; reason: string };

/** 변경 기록 한 쪽의 크기. 이만큼 받아 오면 더 있을 수 있다는 뜻이다. */
const ACTIVITY_PAGE = 50;

/**
 * 조직 화면 v3 — 한 사람을 여섯 축(계층 · 소속 · 직책 · 직급 · 직무 · 권한)으로 읽는 화면.
 *
 * 3분할: 조직 tree → 구성원 목록 → 여섯 축 상세, 그 아래 변경 기록(관리자만).
 *
 * 패널 ③ 는 `GET /api/organization/members/{id}` **한 번**으로 채운다 — 여섯 축·재직·계정 유무·권한·회수된
 * 권한이 한 응답에 있다. 목록 응답과 권한 응답을 프론트에서 이어 붙이지 않는다.
 *
 * 실제 접근을 바꾸는 축은 권한 하나뿐이고, 이 화면에서 바꿀 수 있는 것도 그 하나뿐이다 — 소속·직책을 바꾸는
 * command 가 서버에 아직 없어서 그 축의 「변경」은 그리지 않는다. 행 메뉴도 마찬가지다. 없는 기능을 쓸 수 있는
 * 것처럼 보이게 하지 않는다 (SPEC-005 §5).
 */
export function OrgPage({ personaId, onError }: OrgPageProps) {
  const [profile, setProfile] = useState<OrganizationProfile | null>(null);
  const [units, setUnits] = useState<OrganizationUnitNode[] | null>(null);
  const [treeFailed, setTreeFailed] = useState(false);
  const [selectedUnitId, setSelectedUnitId] = useState<string | null>(null);
  const [expandedUnitIds, setExpandedUnitIds] = useState<Set<string>>(new Set());
  const [members, setMembers] = useState<OrganizationMember[] | null>(null);
  const [membersFailed, setMembersFailed] = useState(false);
  const [selectedMember, setSelectedMember] = useState<OrganizationMember | null>(null);
  const [roles, setRoles] = useState<InstalledAccessRole[]>([]);
  const [detail, setDetail] = useState<DetailView>({ status: "empty" });
  const [activity, setActivity] = useState<OrganizationActivityEvent[] | null>(null);
  const [activityFailed, setActivityFailed] = useState(false);
  const [activityHasMore, setActivityHasMore] = useState(false);
  const [activityLoadingMore, setActivityLoadingMore] = useState(false);
  const [query, setQuery] = useState("");
  /** 이름 검색은 조직이 아니라 사람을 찾는다. 그래서 조직 전체의 명부를 한 번 받아 색인으로 쓴다. */
  const [directory, setDirectory] = useState<OrganizationMember[]>([]);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [pendingRevoke, setPendingRevoke] = useState<PendingRevoke | null>(null);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<{ message: string; tone: "success" | "error" } | null>(null);

  const administers = (profile?.capabilities ?? []).includes("organization.manage");
  /**
   * 권한·회수분·이력을 볼 수 있는가. 서버의 기준과 같게 둔다 — **본인이거나 그 사람을 관리할 수 있는 사람**.
   *
   * 응답의 모양은 누구에게나 같아서(볼 수 없는 축은 값만 비어 온다) 모양으로는 자격을 알 수 없다. 비어 있는
   * `grants` 가 "권한이 없다"인지 "볼 자격이 없다"인지 응답이 구별해 주지 않기 때문이다. 그래서 화면이 같은
   * 기준을 한 번 더 세운다. 관리 범위가 조직 일부인 사람은 여기서 참이어도 서버가 403 을 줄 수 있고,
   * 그때는 Toast 로 말한다.
   */
  const canReadSensitive =
    administers || (profile !== null && selectedMember !== null && profile.member_id === selectedMember.member_id);

  useEffect(() => {
    let cancelled = false;
    void Promise.all([getMyOrganizationProfile(), getOrganizationTree()])
      .then(([nextProfile, tree]) => {
        if (cancelled) return;
        setProfile(nextProfile);
        setUnits(tree);
        setTreeFailed(false);
        // 회사 이름은 고객마다 다르다. 꼭대기는 위로 더 올라갈 곳이 없는 단위이지 특정한 이름이 아니다.
        const root = tree.find((unit) => !unit.parent_id) ?? null;
        const initial = nextProfile.organizations.find((item) => item.id !== root?.id)?.id ?? tree[0]?.id ?? null;
        setSelectedUnitId((current) => current ?? initial);
        // 처음에는 고른 조직까지 가는 길만 펼친다 — 나머지는 접힌 채로 둔다.
        const byId = new Map(tree.map((unit) => [unit.id, unit]));
        const path = new Set<string>();
        let cursor = initial ? byId.get(initial) : undefined;
        while (cursor) {
          path.add(cursor.id);
          cursor = cursor.parent_id ? byId.get(cursor.parent_id) : undefined;
        }
        setExpandedUnitIds(path);
        onError(null);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setTreeFailed(true);
        setUnits([]);
        onError(error instanceof Error ? error.message : "조직 정보를 불러오지 못했습니다.");
      });
    return () => {
      cancelled = true;
    };
  }, [onError, personaId]);

  const rootUnitId = useMemo(() => (units ?? []).find((unit) => !unit.parent_id)?.id ?? null, [units]);

  useEffect(() => {
    if (!rootUnitId) return;
    let cancelled = false;
    void getOrganizationUnitMembers(rootUnitId)
      .then((items) => {
        if (!cancelled) setDirectory(items);
      })
      .catch(() => {
        // 검색 색인을 못 받아도 tree 는 그대로 읽힌다. 검색만 조용히 못 하게 둔다.
        if (!cancelled) setDirectory([]);
      });
    return () => {
      cancelled = true;
    };
  }, [rootUnitId]);

  useEffect(() => {
    if (!selectedUnitId) return;
    let cancelled = false;
    setMembers(null);
    setMembersFailed(false);
    setSelectedMember(null);
    void getOrganizationUnitMembers(selectedUnitId)
      .then((items) => {
        if (!cancelled) setMembers(items);
      })
      .catch(() => {
        if (cancelled) return;
        setMembersFailed(true);
        setMembers([]);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedUnitId]);

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

  /**
   * 한 사람을 여섯 축으로 한 번에 읽는다. 권한·회수분도 이 응답에 있다.
   *
   * 요청마다 세대를 하나 올리고, 돌아왔을 때 세대가 이미 지났으면 **그 응답은 버린다.** A 를 고른 뒤 응답이
   * 오기 전에 B 를 고르면 A 의 응답이 나중에 도착해 상세를 A 로 덮을 수 있었는데, 그러면 화면은 A 를
   * 보여 주면서 권한 변경은 B 에게 나가게 된다 — 화면에 보이는 사람과 바뀌는 사람이 갈라지는 일이다.
   */
  const detailGeneration = useRef(0);
  const loadDetail = useCallback(async (memberId: string | null) => {
    const generation = ++detailGeneration.current;
    if (!memberId) {
      setDetail({ status: "empty" });
      return;
    }
    setDetail((current) => (current.status === "ready" && current.detail.member_id === memberId ? current : { status: "loading" }));
    try {
      const next = await getOrganizationMemberAxes(memberId);
      if (detailGeneration.current !== generation) return;
      setDetail({ status: "ready", detail: next });
    } catch {
      if (detailGeneration.current !== generation) return;
      setDetail({ status: "error" });
    }
  }, []);

  useEffect(() => {
    void loadDetail(selectedMember?.member_id ?? null);
  }, [loadDetail, selectedMember]);

  /**
   * 변경 기록은 고른 조직의 것이다 — 조직을 바꾸면 다시 묻는다.
   *
   * 서버는 봉투 없이 배열만 준다. 받은 수가 요청한 수와 같으면 더 있을 수 있다는 뜻이라, 마지막 행의 시각을
   * 다음 쪽의 cursor 로 쓴다.
   */
  const activityGeneration = useRef(0);
  const loadActivity = useCallback(
    async (unitId: string | null) => {
      if (!administers) return;
      const generation = ++activityGeneration.current;
      setActivity(null);
      setActivityFailed(false);
      setActivityHasMore(false);
      try {
        const events = await getOrganizationActivity({ limit: ACTIVITY_PAGE, unitId });
        // 조직을 바꾸면 세대가 올라간다 — 이전 조직의 첫 쪽이 늦게 와도 새 표를 덮지 않는다.
        if (activityGeneration.current !== generation) return;
        setActivity(events);
        setActivityHasMore(events.length === ACTIVITY_PAGE);
      } catch {
        if (activityGeneration.current !== generation) return;
        setActivityFailed(true);
        setActivity([]);
      }
    },
    [administers],
  );

  useEffect(() => {
    void loadActivity(selectedUnitId);
  }, [loadActivity, selectedUnitId]);

  /**
   * 「더 보기」는 새 세대를 열지 않고 지금 세대에 이어 붙인다 — 그 사이 조직이 바뀌었으면 붙이지 않는다.
   *
   * 다음 쪽의 자리표는 마지막 행이 실어 온 `cursor` 를 **그대로** 되보낸다. 시각만 보내면 같은 시각의
   * 나머지 사건이 통째로 빠진다 (PR #2 F4 — 서버 정렬이 (시각, id) 이므로 경계도 그 둘이다).
   */
  async function loadMoreActivity() {
    const last = activity?.[activity.length - 1];
    if (!last || activityLoadingMore) return;
    const generation = activityGeneration.current;
    setActivityLoadingMore(true);
    try {
      const next = await getOrganizationActivity({ cursor: last.cursor, limit: ACTIVITY_PAGE, unitId: selectedUnitId });
      if (activityGeneration.current !== generation) return;
      setActivity((current) => [...(current ?? []), ...next]);
      setActivityHasMore(next.length === ACTIVITY_PAGE);
    } catch {
      if (activityGeneration.current !== generation) return;
      setToast({ message: orgScreen.changeLogError, tone: "error" });
    } finally {
      setActivityLoadingMore(false);
    }
  }

  const childrenOf = useMemo(() => {
    const map = new Map<string | null, OrganizationUnitNode[]>();
    for (const unit of units ?? []) {
      const list = map.get(unit.parent_id) ?? [];
      list.push(unit);
      map.set(unit.parent_id, list);
    }
    for (const list of map.values()) list.sort((a, b) => a.display_order - b.display_order || a.id.localeCompare(b.id));
    return map;
  }, [units]);
  const unitById = useMemo(() => new Map((units ?? []).map((unit) => [unit.id, unit])), [units]);
  const selectedUnit = selectedUnitId ? unitById.get(selectedUnitId) ?? null : null;

  /** 검색 중일 때 tree 에 남길 조직 — 이름이 맞는 사람이 속한 조직과 거기까지 가는 길. */
  const visibleUnitIds = useMemo(() => {
    const needle = query.trim();
    if (!needle) return null;
    const keep = new Set<string>();
    for (const member of directory) {
      if (!personName(member.display_name).includes(needle)) continue;
      for (const membership of member.memberships) {
        let cursor = unitById.get(membership.organization_id);
        while (cursor && !keep.has(cursor.id)) {
          keep.add(cursor.id);
          cursor = cursor.parent_id ? unitById.get(cursor.parent_id) : undefined;
        }
      }
    }
    return keep;
  }, [directory, query, unitById]);

  const toggleUnit = useCallback((unitId: string) => {
    setExpandedUnitIds((current) => {
      const next = new Set(current);
      if (next.has(unitId)) next.delete(unitId);
      else next.add(unitId);
      return next;
    });
  }, []);

  /**
   * 바꾸는 대상은 **Drawer 가 보여 주고 있는 그 사람**이다 — 목록의 선택이 아니라 상세 응답 하나에서 읽는다.
   * 표시와 변경이 서로 다른 상태를 보면 둘이 갈라질 수 있고, 그 갈라짐이 남의 권한을 바꾼다.
   */
  const changeTarget = detail.status === "ready" ? detail.detail : null;

  async function grant(input: { roleId: string; scopeRef: string; reason: string }) {
    if (!changeTarget || busy) return;
    setBusy(true);
    onError(null);
    try {
      await grantAccessRole({
        member_id: changeTarget.member_id,
        role_id: input.roleId,
        scope_kind: input.scopeRef === rootUnitId ? "organization" : "unit",
        scope_ref: input.scopeRef,
        include_descendants: true,
        reason: input.reason,
      });
      setDrawerOpen(false);
      await Promise.all([loadDetail(changeTarget.member_id), loadActivity(selectedUnitId)]);
      setToast({ message: orgScreen.accessDrawer.grantSuccess, tone: "success" });
    } catch (error) {
      setToast({ message: error instanceof Error ? error.message : orgScreen.accessDrawer.grantFailure, tone: "error" });
    } finally {
      setBusy(false);
    }
  }

  async function revoke({ grant: target, reason }: PendingRevoke) {
    if (!changeTarget || busy) return;
    setBusy(true);
    onError(null);
    try {
      await revokeAccessGrant(target.grant_id, reason);
      setPendingRevoke(null);
      await Promise.all([loadDetail(changeTarget.member_id), loadActivity(selectedUnitId)]);
      setToast({ message: orgScreen.accessDrawer.revokeSuccess, tone: "success" });
    } catch (error) {
      setPendingRevoke(null);
      setToast({ message: error instanceof Error ? error.message : orgScreen.accessDrawer.revokeFailure, tone: "error" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="page-surface">
      <div className="page-head">
        <div>
          <h1>{orgScreen.title}</h1>
        </div>
        <div className="page-head-actions">
          {administers ? (
            <span className="badge ai">{orgScreen.adminBadge}</span>
          ) : (
            <span className="badge outline">{orgScreen.readOnlyBadge}</span>
          )}
        </div>
      </div>

      <div className="org-screen-grid">
        <OrgTreePanel
          childrenOf={childrenOf}
          expandedUnitIds={expandedUnitIds}
          failed={treeFailed}
          loading={units === null}
          onQueryChange={setQuery}
          onSelect={setSelectedUnitId}
          onToggle={toggleUnit}
          query={query}
          selectedUnitId={selectedUnitId}
          visibleUnitIds={visibleUnitIds}
        />
        <MemberListPanel
          failed={membersFailed}
          members={members}
          onSelect={setSelectedMember}
          selectedMemberId={selectedMember?.member_id ?? null}
          unit={selectedUnit}
        />
        <MemberAxesPanel
          canManageAccess={administers}
          canReadSensitive={canReadSensitive}
          onChangeAccess={() => setDrawerOpen(true)}
          onHistoryDenied={(message) => setToast({ message, tone: "error" })}
          view={detail}
        />
      </div>

      {administers && (
        <ChangeLogPanel
          events={activity}
          failed={activityFailed}
          hasMore={activityHasMore}
          loadingMore={activityLoadingMore}
          onLoadMore={() => void loadMoreActivity()}
        />
      )}

      {drawerOpen && detail.status === "ready" && (
        <AccessDrawer
          busy={busy}
          grants={detail.detail.grants}
          member={detail.detail}
          onClose={() => setDrawerOpen(false)}
          onGrant={(input) => void grant(input)}
          onRequestRevoke={(target, reason) => {
            // Drawer 위에 모달을 겹치지 않는다 — 먼저 닫고 나서 확인을 띄운다.
            setDrawerOpen(false);
            setPendingRevoke({ grant: target, reason });
          }}
          roles={roles}
          rootUnitId={rootUnitId}
          units={units ?? []}
        />
      )}

      {pendingRevoke && (
        <ConfirmModal
          busy={busy}
          confirmLabel={orgScreen.accessDrawer.confirmLabel}
          danger
          description={`${grantText(pendingRevoke.grant)} — ${pendingRevoke.reason}`}
          onClose={() => setPendingRevoke(null)}
          onConfirm={() => void revoke(pendingRevoke)}
          title={orgScreen.accessDrawer.confirmTitle}
        />
      )}

      {toast && <Toast message={toast.message} onClose={() => setToast(null)} tone={toast.tone} />}
    </section>
  );
}
