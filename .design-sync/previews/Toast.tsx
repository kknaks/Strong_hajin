import { Toast } from "ax-workspace-frontend";

/**
 * Storybook 메타. design-sync 컨버터는 대문자로 시작하는 named export 만 카드 셀로 세므로
 * (`lib/emit.mjs` 의 `/^[A-Z]/` 필터) 이 default export 는 프리뷰 카드에 영향을 주지 않는다.
 * 스토리를 더하려면 이 파일에 named export 를 하나 더 쓰면 된다 — 두 곳에 같이 반영된다.
 */
export default { title: "General/Toast", component: Toast };


/** 알림 한 줄 — 400 × 56 · 하단 중앙 60px 위 (v2 14) */
export const Basic = () => (
  <div style={{ minHeight: 360 }}>
    <Toast message="업무를 저장했어요" onClose={() => {}} />
  </div>
);

/** 되돌릴 수 있는 동작이면 실행취소를 함께 */
export const WithAction = () => (
  <div style={{ minHeight: 360 }}>
    <Toast message="업무 1건을 취소했어요" action={{ label: "실행취소", onAction: () => {} }} onClose={() => {}} />
  </div>
);

/** 완료 — check 아이콘을 --accent 로 (v2 16-2 「완료는 accent」, 초록 없음) */
export const Success = () => (
  <div style={{ minHeight: 360 }}>
    <Toast tone="success" message="완료 확인을 요청했어요" onClose={() => {}} />
  </div>
);

/** 실패 — alert 아이콘을 --danger-accent 로, role=alert */
export const Error = () => (
  <div style={{ minHeight: 360 }}>
    <Toast tone="error" message="저장하지 못했어요. 다시 시도해 주세요." onClose={() => {}} />
  </div>
);
