import { useEffect, useState } from "react";

import { getOrganizationTree, getOrganizationUnitMembers } from "../api";
import { personName } from "../labels";

/**
 * 사람을 고르는 자리(예약 · 공유 · 머리 편집)가 함께 쓰는 명부.
 *
 * 회의 API 는 사람 명부를 주지 않는다 (WP-001 범위 밖) — 조직 조회를 그대로 쓴다. 부서를 먼저 받고
 * 부서마다 구성원을 받아 한 벌로 편다. 화면 셋이 같은 명부를 봐야 「이름 · 부서 · 직급」이 갈리지 않는다.
 */
export type RosterUnit = { id: string; name: string; count: number };
export type RosterPerson = { member_id: string; name: string; unit_id: string; unit: string; rank: string };
export type Roster = { units: RosterUnit[]; people: RosterPerson[] };

let pending: Promise<Roster> | null = null;

/** 테스트가 앞 케이스의 명부를 물려받지 않게 한다. */
export function resetRoster() {
  pending = null;
}

async function load(): Promise<Roster> {
  const tree = await getOrganizationTree();
  const units = tree.map((node) => ({ id: node.id, name: node.name, count: node.member_count }));
  const rosters = await Promise.all(
    units.map(async (unit) => {
      const members = await getOrganizationUnitMembers(unit.id).catch(() => []);
      return members.map((member) => ({
        member_id: member.member_id,
        name: personName(member.display_name),
        unit_id: unit.id,
        unit: unit.name,
        // 직책이 있으면 직책이 그 사람을 더 잘 가리킨다. 없으면 직급을 낸다.
        rank: member.positions[0]?.position ?? member.grade ?? "",
      }));
    }),
  );
  return { units, people: rosters.flat() };
}

export function loadRoster(): Promise<Roster> {
  if (!pending) pending = load();
  return pending;
}

/** 명부는 한 번만 받는다. 못 받으면 `failed` 로 말하고 고르는 자리를 비워 둔다 — 지어내지 않는다. */
export function useRoster(active: boolean): { roster: Roster | null; failed: boolean } {
  const [roster, setRoster] = useState<Roster | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    void loadRoster()
      .then((next) => {
        if (!cancelled) setRoster(next);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [active]);
  return { roster, failed };
}

/** 이름으로 찾는다 — 부서를 넘어 전부에서. 같은 사람이 여러 부서에 걸려도 한 번만 선다. */
export function searchRoster(roster: Roster | null, query: string, excluded: ReadonlySet<string>): RosterPerson[] {
  const text = query.trim();
  if (!roster || !text) return [];
  const seen = new Set<string>();
  return roster.people.filter((person) => {
    if (!person.name.includes(text) || excluded.has(person.member_id) || seen.has(person.member_id)) return false;
    seen.add(person.member_id);
    return true;
  });
}
