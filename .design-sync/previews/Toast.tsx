import { Toast } from "ax-workspace-frontend";

export default { title: "General/Toast", component: Toast };

/**
 * 4초 뒤 스스로 사라진다. 바퀴 11 이후 `closeLabel` 을 **반드시** 받는다.
 */

/** 알림 한 줄 */
export const Basic = () => (
  <div style={{ minHeight: 360 }}>
    <Toast closeLabel="알림 닫기" message="업무를 저장했어요" onClose={() => {}} />
  </div>
);

/** 되돌릴 수 있는 동작이면 실행취소를 함께 */
export const WithAction = () => (
  <div style={{ minHeight: 360 }}>
    <Toast
      action={{ label: "실행취소", onAction: () => {} }}
      closeLabel="알림 닫기"
      message="업무 1건을 취소했어요"
      onClose={() => {}}
    />
  </div>
);

/** 완료 — check 글리프를 `--scax-color-accent` 로 (완료는 accent, 초록은 이 시스템에 없다) */
export const Success = () => (
  <div style={{ minHeight: 360 }}>
    <Toast closeLabel="알림 닫기" message="완료 확인을 요청했어요" onClose={() => {}} tone="success" />
  </div>
);

/** 실패 — circle-exclamation 글리프를 danger 로, role=alert */
export const Error = () => (
  <div style={{ minHeight: 360 }}>
    <Toast closeLabel="알림 닫기" message="저장하지 못했어요. 다시 시도해 주세요." onClose={() => {}} tone="error" />
  </div>
);
