import { useState } from "react";
import { ChipRow, ChipToggle } from "ax-workspace-frontend";

export default { title: "General/ChipToggle", component: ChipToggle };

/**
 * `Chip` 과 다른 물건이다 — 안에 진짜 `<input type="checkbox">` 가 있는 **폼 값**이라
 * 키보드로도 켜고 끈다.
 */
export const MultiPick = () => {
  const [picked, setPicked] = useState<string[]>(["회의록", "액션 아이템"]);
  const toggle = (name: string, on: boolean) =>
    setPicked((prev) => (on ? [...prev, name] : prev.filter((item) => item !== name)));
  return (
    <div className="surface-card" style={{ width: 460 }}>
      <p className="t-item" style={{ marginBottom: 10 }}>공유할 항목</p>
      <ChipRow>
        {["회의록", "원문 스크립트", "액션 아이템", "첨부 자료"].map((name) => (
          <ChipToggle checked={picked.includes(name)} key={name} onChange={(on) => toggle(name, on)}>
            {name}
          </ChipToggle>
        ))}
      </ChipRow>
      <p className="t-meta" style={{ marginTop: 12 }}>{picked.length}개 선택됨</p>
    </div>
  );
};

/** 못 고르는 자리 */
export const Disabled = () => (
  <div className="surface-card" style={{ width: 460 }}>
    <ChipRow>
      <ChipToggle checked onChange={() => {}}>회의록</ChipToggle>
      <ChipToggle checked={false} disabled onChange={() => {}}>원문 스크립트 (권한 없음)</ChipToggle>
    </ChipRow>
  </div>
);
