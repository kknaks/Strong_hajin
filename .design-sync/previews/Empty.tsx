import { Empty } from "ax-workspace-frontend";

export default { title: "General/Empty", component: Empty };

/**
 * 바퀴 11: **행동과 그 이름은 한 쌍이다.** 타입이 둘을 묶어 두어서, 이름 없이 `onAction` 만
 * 넘기면 컴파일이 안 된다 — 예전에는 `variant` 가 `filter`·`error` 면 부품이 스스로 이름을
 * 골라 왔지만(`lib/labels` 의 `emptyActionLabel`) 이제 부품은 말을 모른다.
 */

const panel: React.CSSProperties = { width: 520 };

/** 비어 있음 — 기본 variant 는 행동 이름을 받아야 한다 */
export const Default = () => (
  <div className="surface-card" style={panel}>
    <Empty title="아직 등록된 업무가 없어요" description="첫 업무를 등록하면 여기에 쌓입니다." actionLabel="새 업무" onAction={() => {}} />
  </div>
);

/** 찾지 못함 — 필터 때문에 비었으면 초기화가 따라온다. 바퀴 11 이후 그 이름도 부르는 쪽이 준다 */
export const Filter = () => (
  <div className="surface-card" style={panel}>
    <Empty variant="filter" title="조건에 맞는 업무가 없어요" description="필터를 바꾸거나 초기화해 보세요." actionLabel="필터 초기화" onAction={() => {}} />
  </div>
);

/** 실패 — 「비었다」가 아니라 `.scax-status-note` 로 그려지고 role=alert 다 */
export const Error = () => (
  <div className="surface-card" style={panel}>
    <Empty variant="error" title="업무 목록을 불러오지 못했어요" description="네트워크 상태를 확인한 뒤 다시 시도해 주세요." actionLabel="다시 시도" onAction={() => {}} />
  </div>
);

/** 행동 없이 설명만 */
export const NoAction = () => (
  <div className="surface-card" style={panel}>
    <Empty title="오늘 회의가 없어요" description="회의가 잡히면 회의록 초안이 여기에 생깁니다." />
  </div>
);
