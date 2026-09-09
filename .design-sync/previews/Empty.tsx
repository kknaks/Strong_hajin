import { Empty } from "ax-workspace-frontend";

/**
 * Storybook 메타. design-sync 컨버터는 대문자로 시작하는 named export 만 카드 셀로 세므로
 * (`lib/emit.mjs` 의 `/^[A-Z]/` 필터) 이 default export 는 프리뷰 카드에 영향을 주지 않는다.
 * 스토리를 더하려면 이 파일에 named export 를 하나 더 쓰면 된다 — 두 곳에 같이 반영된다.
 */
export default { title: "General/Empty", component: Empty };


const panel: React.CSSProperties = { width: 520 };

/** 비어 있음 — 기본 variant 는 행동 이름을 받아야 한다 */
export const Default = () => (
  <div className="surface-card" style={panel}>
    <Empty title="아직 등록된 업무가 없어요" description="첫 업무를 등록하면 여기에 쌓입니다." actionLabel="새 업무" onAction={() => {}} />
  </div>
);

/** 찾지 못함 — 필터 때문에 비었으면 초기화 버튼이 따라온다 */
export const Filter = () => (
  <div className="surface-card" style={panel}>
    <Empty variant="filter" title="조건에 맞는 업무가 없어요" description="필터를 바꾸거나 초기화해 보세요." onAction={() => {}} />
  </div>
);

/** 실패 — 다시 시도를 둔다 (role=alert) */
export const Error = () => (
  <div className="surface-card" style={panel}>
    <Empty variant="error" title="업무 목록을 불러오지 못했어요" description="네트워크 상태를 확인한 뒤 다시 시도해 주세요." onAction={() => {}} />
  </div>
);

/** 행동 없이 설명만 */
export const NoAction = () => (
  <div className="surface-card" style={panel}>
    <Empty title="오늘 회의가 없어요" description="회의가 잡히면 회의록 초안이 여기에 생깁니다." />
  </div>
);
