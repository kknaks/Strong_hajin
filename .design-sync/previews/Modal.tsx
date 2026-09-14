import { Modal, Button, Checkbox, StatusNote } from "ax-workspace-frontend";

export default { title: "General/Modal", component: Modal };

/**
 * 화면 가운데 모달. `Drawer` 와 달리 뒤 화면을 덮는다.
 * 닫는 길 셋을 다 갖는다 — 머리의 × · Esc · 바깥 클릭.
 */
export const Promote = () => (
  <Modal
    closeLabel="닫기"
    footer={
      <>
        <span className="spacer" />
        <Button variant="text" label="취소" />
        <Button variant="solid" tone="primary" label="업무로 보내기" />
      </>
    }
    label="액션 아이템 승격"
    onClose={() => {}}
    title="액션 아이템을 업무로 보냅니다"
  >
    <p style={{ fontSize: 14, marginBottom: 16 }}>
      회의록에서 뽑은 액션 아이템 3건을 업무로 만듭니다. 담당자와 마감일은 보낸 뒤에도 바꿀 수 있습니다.
    </p>
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <Checkbox checked onChange={() => {}}>회의실 예약 정책 검토 — 정민우</Checkbox>
      <Checkbox checked onChange={() => {}}>회의록 템플릿 확정 — 한지원</Checkbox>
      <Checkbox checked={false} onChange={() => {}}>전사 품질 재확인 — 미지정</Checkbox>
    </div>
    <div style={{ marginTop: 16 }}>
      <StatusNote>보낸 업무는 받는 사람의 「받은 업무」에 바로 뜹니다.</StatusNote>
    </div>
  </Modal>
);

/** `size="sm"` — 갈래가 좁은 자리 */
export const Small = () => (
  <Modal
    closeLabel="닫기"
    footer={
      <>
        <span className="spacer" />
        <Button variant="text" label="취소" />
        <Button variant="solid" tone="primary" label="예약" />
      </>
    }
    label="회의실 예약"
    onClose={() => {}}
    size="sm"
    title="THE CONNECT 3층 A룸"
  >
    <p style={{ fontSize: 14 }}>2026-09-20 14:00 – 15:00 · 8인실</p>
  </Modal>
);
