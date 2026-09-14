import { useState } from "react";
import { SegmentedControl } from "ax-workspace-frontend";

export default { title: "General/SegmentedControl", component: SegmentedControl };

/** 칸 안에서 고른다 — 「같은 것을 다르게 보는」 자리 */
export const ViewMode = () => {
  const [mode, setMode] = useState<"week" | "month">("month");
  return (
    <div className="surface-card" style={{ width: 420 }}>
      <SegmentedControl
        ariaLabel="캘린더 보기 방식"
        onChange={setMode}
        options={[
          { value: "week", label: "주" },
          { value: "month", label: "월" },
        ]}
        value={mode}
      />
      <p className="t-meta" style={{ marginTop: 12 }}>지금 보기: {mode === "week" ? "주" : "월"}</p>
    </div>
  );
};

/** 항목에 `disabled` — 그래프를 불러오는 동안 표현 수준을 못 바꾸게 막는 자리 */
export const WithDisabled = () => {
  const [level, setLevel] = useState<"simple" | "detail" | "full">("simple");
  return (
    <div className="surface-card" style={{ width: 460 }}>
      <SegmentedControl
        ariaLabel="관계 표현 수준"
        onChange={setLevel}
        options={[
          { value: "simple", label: "간단" },
          { value: "detail", label: "자세히" },
          { value: "full", label: "전체", disabled: true },
        ]}
        value={level}
      />
      <p className="t-meta" style={{ marginTop: 12 }}>그래프를 불러오는 동안 「전체」는 고를 수 없다.</p>
    </div>
  );
};
