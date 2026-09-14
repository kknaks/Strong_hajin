import { useState } from "react";
import { Chip, ChipBar, ChipRow, Button } from "ax-workspace-frontend";

export default { title: "General/Chip", component: Chip };

/** 켜고 끄는 낱말 단추 — `aria-pressed` 를 쓰는 단추다 */
export const OnOff = () => (
  <div className="surface-card" style={{ width: 480 }}>
    <ChipRow>
      <Chip label="전체" on />
      <Chip label="진행 중" />
      <Chip label="막힘" />
      <Chip label="완료" />
      <Chip label="취소" disabled />
    </ChipRow>
  </div>
);

/** `ChipBar` — 업무 표 위 툴바. 오른쪽 끝에 붙일 것은 `end` 로 넘긴다 */
export const Toolbar = () => {
  const [on, setOn] = useState("active");
  return (
    <div className="surface-card" style={{ width: 620 }}>
      <ChipBar end={<Button size="sm" iconBefore="plus" label="새 업무" />}>
        <Chip label="진행 중·시작 전·막힘" on={on === "active"} onClick={() => setOn("active")} />
        <Chip label="전체 상태" on={on === "all"} onClick={() => setOn("all")} />
        <Chip label="내가 보낸 것" on={on === "sent"} onClick={() => setOn("sent")} />
      </ChipBar>
    </div>
  );
};

/** `ChipRow` — 줄바꿈되는 줄. `ChipBar` 와 달리 최소 폭·패딩이 없다 */
export const Wrapping = () => (
  <div className="surface-card" style={{ width: 380 }}>
    <ChipRow>
      <Chip label="회의록" on />
      <Chip label="일일보고" />
      <Chip label="주간 업무보고" on />
      <Chip label="액션 아이템" />
      <Chip label="요청 승인" />
      <Chip label="자료 추출" />
    </ChipRow>
  </div>
);
