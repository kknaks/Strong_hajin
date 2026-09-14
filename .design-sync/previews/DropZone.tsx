import { DropZone } from "ax-workspace-frontend";

export default { title: "General/DropZone", component: DropZone };

/**
 * 파일을 끌어다 놓거나 골라 받는 자리.
 * **받는 것은 이 부품이 판단하지 않는다** — 크기·형식은 부르는 쪽과 서버가 정한다.
 */
export const MeetingFiles = () => (
  <div className="surface-card" style={{ width: 560 }}>
    <DropZone
      accept=".pdf,.md,.txt,.docx"
      hint="한 건당 20MB · PDF · Markdown · Word"
      onFiles={() => {}}
      pickLabel="파일 고르기"
    >
      <p className="t-item">회의 자료를 여기에 끌어다 놓으세요</p>
    </DropZone>
  </div>
);

/** 고르는 말만 — 위에 서는 말은 선택이다 */
export const Bare = () => (
  <div className="surface-card" style={{ width: 560 }}>
    <DropZone hint="이미지 · 최대 10MB" onFiles={() => {}} pickLabel="증빙 첨부" />
  </div>
);

/** 못 받는 자리 */
export const Disabled = () => (
  <div className="surface-card" style={{ width: 560 }}>
    <DropZone
      disabled
      hint="회의가 끝난 뒤에는 자료를 더할 수 없습니다"
      onFiles={() => {}}
      pickLabel="파일 고르기"
    >
      <p className="t-meta">종료된 회의</p>
    </DropZone>
  </div>
);
