import { Button } from "ax-workspace-frontend";

export default { title: "General/Button", component: Button };

const row: React.CSSProperties = { display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8 };
const card: React.CSSProperties = { width: 520 };

/** 변형 셋 × 톤 — 화면당 solid primary 는 하나 */
export const Variants = () => (
  <div className="surface-card" style={card}>
    <div style={{ ...row, marginBottom: 12 }}>
      <Button variant="solid" tone="primary" label="업무 보내기" />
      <Button variant="outlined" label="임시 저장" />
      <Button variant="text" label="취소" />
      <Button variant="solid" tone="danger" label="삭제" />
    </div>
    <div style={row}>
      <Button variant="outlined" tone="danger" label="반려" />
      <Button variant="outlined" label="닫기" disabled />
    </div>
  </div>
);

/** 크기 셋 */
export const Sizes = () => (
  <div className="surface-card" style={card}>
    <div style={row}>
      <Button size="sm" label="작게" />
      <Button size="md" label="기본" />
      <Button size="lg" label="크게" />
    </div>
  </div>
);

/** 앞 글리프 · 줄 폭 전부 */
export const WithIcon = () => (
  <div className="surface-card" style={card}>
    <div style={{ ...row, marginBottom: 12 }}>
      <Button iconBefore="plus" label="새 업무" />
      <Button iconBefore="paperclip" label="자료 첨부" />
      <Button iconBefore="reset" label="다시 시도" />
    </div>
    <Button block variant="solid" tone="primary" iconBefore="send" label="회의록 공유하기" />
  </div>
);

/**
 * `inline` — 글줄 속 단추 (`DS-gaps` 신규).
 * 높이·여백·테두리가 없어 문장 안에 글자처럼 선다. 다른 변형은 34px 컨트롤이라 줄 높이가 튄다.
 */
export const Inline = () => (
  <div className="surface-card" style={card}>
    <p style={{ fontSize: 14, lineHeight: 1.7 }}>
      이 회의록은 <Button variant="inline" label="원문 03:14" /> 구간을 근거로 작성됐습니다.
      내용이 다르면 <Button variant="inline" label="다시 전사" /> 하거나{" "}
      <Button variant="inline" label="메모 트랙으로 전환" /> 하세요.
    </p>
  </div>
);

/**
 * `ai` — AX 표면의 단추 (`DS-gaps` G-17 · `--scax-ai-*`).
 * 「AX 가 하는 일」 하나라 톤을 갈래로 두지 않는다.
 */
export const Ai = () => (
  <div className="surface-card" style={card}>
    <div style={row}>
      <Button variant="ai" iconBefore="sparkle" label="AI 요약 생성" />
      <Button variant="ai" iconBefore="sparkle" label="액션 아이템 뽑기" />
    </div>
  </div>
);
