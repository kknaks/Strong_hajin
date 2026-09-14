import { Badge } from "ax-workspace-frontend";

export default { title: "General/Badge", component: Badge };

const row: React.CSSProperties = { display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8 };

/** 톤 다섯 — components.css 에 실제로 있는 것만 */
export const Tones = () => (
  <div className="surface-card" style={{ width: 460 }}>
    <div style={row}>
      <Badge tone="accent">AX 작성</Badge>
      <Badge tone="neutral">메모</Badge>
      <Badge tone="info">진행 중</Badge>
      <Badge tone="positive">승인됨</Badge>
      <Badge tone="danger">반려</Badge>
    </div>
  </div>
);

/**
 * `outline` — 바퀴 9 가 더한 톤 (`DS-gaps` G-29).
 * 채움형 neutral 과 뜻이 다르다: 「아직 확정 아닌 값」을 가리킨다.
 */
export const Outline = () => (
  <div className="surface-card" style={{ width: 460 }}>
    <div style={{ ...row, marginBottom: 12 }}>
      <Badge tone="outline">초안 v3</Badge>
      <Badge tone="outline">공유 링크</Badge>
      <Badge tone="outline">색인 없음</Badge>
    </div>
    <p className="t-meta">테두리형은 「확정되지 않은 값」 — 채움형 neutral(메모·종류)과 같은 자리에 쓰지 않는다.</p>
  </div>
);

/** `variant="count"` — 안 읽은 개수를 세는 빨간 원. `tone` 을 무시한다 */
export const Count = () => (
  <div className="surface-card" style={{ width: 460 }}>
    <div style={row}>
      <span className="t-item">받은 업무</span>
      <Badge variant="count">3</Badge>
      <span className="t-item" style={{ marginLeft: 16 }}>회의록 검토</span>
      <Badge variant="count">12</Badge>
    </div>
  </div>
);

/** 남은 `<span>` 속성은 전부 통과한다 — 자료 추출 상태 배지가 `data-status`·`title` 을 단다 */
export const WithAttributes = () => (
  <div className="surface-card" style={{ width: 460 }}>
    <div style={row}>
      <Badge tone="positive" data-status="extracted" title="2026-09-13 14:02 추출 완료">추출 완료</Badge>
      <Badge tone="danger" data-status="failed" title="PDF 암호가 걸려 있습니다">추출 실패</Badge>
    </div>
  </div>
);
