import { ConfirmModal } from "ax-workspace-frontend";

/**
 * Storybook 메타. design-sync 컨버터는 대문자로 시작하는 named export 만 카드 셀로 세므로
 * (`lib/emit.mjs` 의 `/^[A-Z]/` 필터) 이 default export 는 프리뷰 카드에 영향을 주지 않는다.
 * 스토리를 더하려면 이 파일에 named export 를 하나 더 쓰면 된다 — 두 곳에 같이 반영된다.
 */
export default { title: "General/ConfirmModal", component: ConfirmModal };


/** 되돌리기 어려운 결정 하나 — 모달 (600) */
export const Confirm = () => (
  <div style={{ minHeight: 560 }}>
    <ConfirmModal
      title="완료 확인을 요청할까요?"
      description="요청자에게 완료 확인 알림이 갑니다. 확인이 끝나기 전까지 업무를 수정할 수 없어요."
      confirmLabel="요청 보내기"
      onConfirm={() => {}}
      onClose={() => {}}
    />
  </div>
);

/** 파괴적 결정 — danger 버튼 */
export const Danger = () => (
  <div style={{ minHeight: 560 }}>
    <ConfirmModal
      title="업무를 삭제할까요?"
      description="「제품 소개서 내용 업데이트」와 체크리스트 5개가 함께 지워집니다. 되돌릴 수 없어요."
      confirmLabel="삭제"
      danger
      onConfirm={() => {}}
      onClose={() => {}}
    />
  </div>
);

/** 처리 중 — 두 버튼 모두 비활성 */
export const Busy = () => (
  <div style={{ minHeight: 560 }}>
    <ConfirmModal
      title="완료 확인을 요청할까요?"
      description="요청자에게 완료 확인 알림이 갑니다."
      confirmLabel="요청 보내기"
      busy
      onConfirm={() => {}}
      onClose={() => {}}
    />
  </div>
);
