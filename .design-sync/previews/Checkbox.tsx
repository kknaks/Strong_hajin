import { Checkbox } from "ax-workspace-frontend";
import { useState } from "react";

/**
 * Storybook 메타. design-sync 컨버터는 대문자로 시작하는 named export 만 카드 셀로 세므로
 * (`lib/emit.mjs` 의 `/^[A-Z]/` 필터) 이 default export 는 프리뷰 카드에 영향을 주지 않는다.
 * 스토리를 더하려면 이 파일에 named export 를 하나 더 쓰면 된다 — 두 곳에 같이 반영된다.
 */
export default { title: "General/Checkbox", component: Checkbox };


const stack: React.CSSProperties = { display: "grid", gap: 12 };

/** 16 × 16 · r4 — 네이티브 input 위에 다시 칠한 것 (v2 09) */
export const States = () => {
  const [a, setA] = useState(false);
  const [b, setB] = useState(true);
  return (
    <div style={stack}>
      <Checkbox checked={a} onChange={setA}>회의록 초안 검토</Checkbox>
      <Checkbox checked={b} onChange={setB}>소개서 5장 수정</Checkbox>
      <Checkbox checked={false} disabled onChange={() => {}}>디자인 검토 (담당자만)</Checkbox>
      <Checkbox checked disabled onChange={() => {}}>2분기 실적 반영 (완료 확인됨)</Checkbox>
    </div>
  );
};

/** 체크리스트 — 업무 안의 단계들 */
export const Checklist = () => {
  const [done, setDone] = useState<Record<string, boolean>>({ a: true, b: true, c: false, d: false, e: false });
  const items = [
    ["a", "요청 내용 확인"], ["b", "2분기 수치 취합"], ["c", "5·6장 본문 다시 쓰기"], ["d", "디자인 팀 검토 요청"], ["e", "최종본 공유"],
  ] as const;
  return (
    <div className="surface-card" style={{ width: 400 }}>
      <div className="card-title"><h3>체크리스트</h3><span className="checklist-progress">2 / 5</span></div>
      <div style={stack}>
        {items.map(([id, label]) => (
          <Checkbox key={id} id={id} checked={done[id]} onChange={(v) => setDone({ ...done, [id]: v })}>{label}</Checkbox>
        ))}
      </div>
    </div>
  );
};

/** 레이블 없이 — 테이블 행 선택용 */
export const Bare = () => {
  const [on, setOn] = useState(true);
  return <Checkbox checked={on} onChange={setOn} />;
};
