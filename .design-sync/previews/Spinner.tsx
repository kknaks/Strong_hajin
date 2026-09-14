import { Skeleton, Spinner } from "ax-workspace-frontend";

export default { title: "General/Spinner", component: Spinner };

/**
 * `Skeleton` 과 갈리는 축은 **「올 것의 모양을 아는가」** 다.
 * 모양을 알면 스켈레톤(줄 수·높이를 실제와 같게), 모르면 이 부품이다.
 *
 * 바퀴 11: `label` 은 화면에 보이는 한 줄이자 접근 이름이다 — 부르는 쪽이 준다.
 */

/** 끝을 모르는 기다림 — 몇 줄이 올지 아무도 모르는 자리 */
export const Waiting = () => (
  <div className="surface-card" style={{ width: 420 }}>
    <Spinner label="회의록을 합성하는 중…" />
  </div>
);

/** 크기 — 기본 20 */
export const Sizes = () => (
  <div className="surface-card" style={{ width: 420, display: "grid", gap: 12 }}>
    <Spinner label="불러오는 중" size={14} />
    <Spinner label="불러오는 중" />
    <Spinner label="불러오는 중" size={28} />
  </div>
);

/** 둘을 나란히 — 같은 자리에 쓰지 않는다 */
export const VersusSkeleton = () => (
  <div className="surface-card" style={{ width: 480, display: "grid", gap: 20 }}>
    <div>
      <p className="t-meta" style={{ marginBottom: 8 }}>모양을 안다 → Skeleton (목록은 5행)</p>
      <Skeleton label="업무 목록 불러오는 중" rows={3} />
    </div>
    <div>
      <p className="t-meta" style={{ marginBottom: 8 }}>모양을 모른다 → Spinner</p>
      <Spinner label="회의록을 합성하는 중…" />
    </div>
  </div>
);
