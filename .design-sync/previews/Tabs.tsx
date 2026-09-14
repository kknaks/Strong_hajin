import { useState } from "react";
import { Tabs } from "ax-workspace-frontend";

export default { title: "General/Tabs", component: Tabs };

/**
 * 면을 가르며 고른다 — 밑줄이 서는 페이지 단위 탭.
 * `.scax-tabs` 는 min-width 880 · 좌우 여백 24 · 아래 경계선을 스스로 갖는 「본문 칸 폭」 부품이라
 * `SegmentedControl` 과 크기가 다르다.
 */
export const PageTabs = () => {
  const [tab, setTab] = useState<"notes" | "script" | "files" | "actions">("notes");
  return (
    <div style={{ width: 940 }}>
      <Tabs
        ariaLabel="회의 상세 탭"
        onChange={setTab}
        options={[
          { value: "notes", label: "회의록" },
          { value: "script", label: "원문" },
          { value: "files", label: "자료" },
          { value: "actions", label: "액션 아이템" },
        ]}
        value={tab}
      />
      <div style={{ padding: "20px 24px" }}>
        <p className="t-meta">지금 탭: {tab}</p>
      </div>
    </div>
  );
};

/** 못 여는 탭 */
export const WithDisabled = () => (
  <div style={{ width: 940 }}>
    <Tabs
      ariaLabel="프로젝트 탭"
      onChange={() => {}}
      options={[
        { value: "overview", label: "개요" },
        { value: "tasks", label: "업무" },
        { value: "access", label: "권한", disabled: true },
      ]}
      value="overview"
    />
  </div>
);
