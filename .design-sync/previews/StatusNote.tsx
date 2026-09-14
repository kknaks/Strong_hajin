import { StatusNote } from "ax-workspace-frontend";

export default { title: "General/StatusNote", component: StatusNote };

/**
 * 지금 무슨 일이 벌어지는지 한 줄로 말하는 자리 (`DS-gaps` G-25).
 * `.error-banner` 는 화면 폭을 먹는 띠라 너무 크고, `Toast` 는 4초 뒤 사라져
 * 「지금 상태」를 계속 말하지 못한다. `role="status"` — 이 줄은 조작이 아니다.
 */
export const Muted = () => (
  <div className="surface-card" style={{ width: 460 }}>
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <StatusNote>실시간 전사에 연결하는 중…</StatusNote>
      <StatusNote>마지막 저장 09:41</StatusNote>
      <StatusNote>2-pass 재전사 대기 중 (3분 전 요청)</StatusNote>
    </div>
  </div>
);

/** 실패는 읽던 자리를 끊지 않는다 */
export const Danger = () => (
  <div className="surface-card" style={{ width: 460 }}>
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <StatusNote tone="danger">전사 서버와 연결이 끊겼습니다. 다시 붙이는 중…</StatusNote>
      <StatusNote tone="danger">자동 저장 실패 — 메모는 이 창에 남아 있습니다.</StatusNote>
    </div>
  </div>
);
