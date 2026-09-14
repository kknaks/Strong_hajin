import { ConfirmModal } from "ax-workspace-frontend";

export default { title: "General/ConfirmModal", component: ConfirmModal };

/**
 * 바퀴 11 이후 이 부품은 말을 모른다 — `cancelLabel`·`closeLabel` 을 **반드시** 받는다.
 */

/** 되돌리기 어려운 결정 하나 */
export const Confirm = () => (
  <div style={{ minHeight: 560 }}>
    <ConfirmModal
      cancelLabel="취소"
      closeLabel="닫기"
      confirmLabel="요청 보내기"
      description="요청자에게 완료 확인 알림이 갑니다. 확인이 끝나기 전까지 업무를 수정할 수 없어요."
      onClose={() => {}}
      onConfirm={() => {}}
      title="완료 확인을 요청할까요?"
    />
  </div>
);

/** 파괴적 결정 — danger 단추 */
export const Danger = () => (
  <div style={{ minHeight: 560 }}>
    <ConfirmModal
      cancelLabel="취소"
      closeLabel="닫기"
      confirmLabel="삭제"
      danger
      description="「제품 소개서 내용 업데이트」와 체크리스트 5개가 함께 지워집니다. 되돌릴 수 없어요."
      onClose={() => {}}
      onConfirm={() => {}}
      title="업무를 삭제할까요?"
    />
  </div>
);

/** 처리 중 — 두 단추 모두 비활성 */
export const Busy = () => (
  <div style={{ minHeight: 560 }}>
    <ConfirmModal
      busy
      cancelLabel="취소"
      closeLabel="닫기"
      confirmLabel="요청 보내기"
      description="요청자에게 완료 확인 알림이 갑니다."
      onClose={() => {}}
      onConfirm={() => {}}
      title="완료 확인을 요청할까요?"
    />
  </div>
);

/**
 * `cancelLabel={null}` — 갈래를 두지 않는 자리(회의 삭제 T08).
 * 푸터에 위험 단추 하나만 서고 **머리에 × 가 선다** — 닫을 길은 반드시 남는다.
 */
export const NoCancel = () => (
  <div style={{ minHeight: 560 }}>
    <ConfirmModal
      cancelLabel={null}
      closeLabel="닫기"
      confirmLabel="회의 삭제"
      danger
      description="회의록 · 원문 스크립트 · 첨부 자료가 함께 지워집니다. 되돌릴 수 없어요."
      onClose={() => {}}
      onConfirm={() => {}}
      title="회의를 삭제할까요?"
    />
  </div>
);
