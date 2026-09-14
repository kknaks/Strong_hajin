import { Icon, Select } from "ax-workspace-frontend";
import { useEffect, useRef, useState } from "react";

export default { title: "General/Select", component: Select };

/* 바퀴 11: `src/ds/` 안에는 한국어가 0이다 — 화면에 나가는 말은 전부 prop 이다.
   `labels` 와 `emptyActionLabel` 은 **필수**다. 키와 값은 `src/lib/labels.ts` 의 `selectLabel` 과 같다. */
const labels = {
  placeholder: "선택",
  search: "검색",
  noMatch: "조건에 맞는 항목이 없습니다",
  clear: "지우기",
  selectAll: "전체 선택",
  clearAll: "전체 해제",
};
const emptyActionLabel = "검색어 지우기";
const words = { labels, emptyActionLabel };

const PROJECTS = [
  { value: "ax-core", label: "기업 AX 코어", description: "2026 상반기", group: "진행 중" },
  { value: "ax-sw", label: "SW개발 전략", description: "2026 상반기", group: "진행 중" },
  { value: "ax-data", label: "데이터 플랫폼", group: "진행 중" },
  { value: "ax-ops", label: "운영 자동화", group: "진행 중" },
  { value: "ax-mfg", label: "제조 지능화", group: "진행 중" },
  { value: "ax-legacy", label: "레거시 이관", group: "보류" },
  { value: "ax-edu", label: "사내 교육", group: "보류" },
  { value: "ax-arch", label: "아키텍처 개편", group: "보류" },
  { value: "ax-old", label: "2025 파일럿", disabled: true, group: "종료" },
];

const OWNERS = [
  { value: "kim", label: "김서연", description: "플랫폼" },
  { value: "lee", label: "이준호", description: "데이터" },
  { value: "park", label: "박민지", description: "디자인" },
];

/** 디자인 시스템 카드용: 마운트 직후 트리거를 눌러 열고, 닫히면 다시 연다 — 카드에서만 쓴다.
 *  패널 클래스는 바퀴 3a 에서 `.popover` → `.scax-popover` 가 됐다. */
function Opened({ children, minHeight = 360 }: { children: React.ReactNode; minHeight?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const open = () => {
      const root = ref.current;
      if (!root || root.querySelector(".scax-popover")) return;
      root.querySelector<HTMLButtonElement>("button[aria-haspopup]")?.click();
    };
    open();
    const timer = window.setInterval(open, 400);
    return () => window.clearInterval(timer);
  }, []);
  return <div ref={ref} style={{ minHeight, padding: 4 }}>{children}</div>;
}

/** 열린 상태 — 그룹 머리글 · 8개 초과라 검색 자동 · footerAction */
export const Open = () => {
  const [v, setV] = useState("ax-sw");
  return (
    <Opened>
      <div className="field" style={{ width: 360 }}>
        <span>프로젝트</span>
        <Select
          {...words}
          footerAction={{ label: "새 프로젝트로 추가", onAction: () => {} }}
          id="sel-project"
          label="프로젝트"
          onChange={setV}
          options={PROJECTS}
          placeholder="프로젝트 선택"
          searchPlaceholder="프로젝트 검색"
          value={v}
        />
      </div>
    </Opened>
  );
};

/** 닫힌 상태 · 값 없음 · 비활성 — 트리거는 폼 필드 규격 그대로 h34 */
export const Triggers = () => {
  const [v, setV] = useState("kim");
  return (
    <div style={{ display: "grid", gap: 16, width: 300, padding: 4 }}>
      <div className="field">
        <span>담당자</span>
        <Select {...words} id="sel-owner" label="담당자" onChange={setV} options={OWNERS} value={v} />
      </div>
      <div className="field">
        <span>참고 업무</span>
        <Select {...words} label="참고 업무" onChange={() => {}} options={OWNERS} placeholder="업무 고르기" value="" />
      </div>
      <div className="field">
        <span>배정자</span>
        <Select {...words} disabled label="배정자" onChange={() => {}} options={OWNERS} placeholder="고를 수 있는 사람이 없습니다" value="" />
      </div>
    </div>
  );
};

/** 에러 — 보더가 갈아입고 헬퍼 대신 한 줄이 나간다 (FieldMessage) */
export const Invalid = () => (
  <div className="field" style={{ width: 300, padding: 4 }}>
    <span>담당자</span>
    <Select {...words} error="담당자를 고르세요" label="담당자" onChange={() => {}} options={OWNERS} placeholder="담당자 고르기" value="" />
  </div>
);

/** 툴바 트리거 — trigger render prop 으로 filter-chip 을 끼운다.
 *  셰브런을 같이 두는 것이 툴바 골격이다 — 빠지면 카드가 규약과 어긋난 예시를 가르친다. */
export const ChipTrigger = () => {
  const [v, setV] = useState("new");
  return (
    <div className="toolbar-group" style={{ padding: 4 }}>
      <Select
        {...words}
        label="정렬"
        onChange={setV}
        options={[
          { value: "new", label: "최신순" },
          { value: "old", label: "오래된순" },
          { value: "due", label: "기한 임박순" },
        ]}
        trigger={({ label, props }) => (
          <button {...props} className="filter-chip">
            정렬 · {label}
            <Icon name="chevron-down" size={12} />
          </button>
        )}
        value={v}
      />
    </div>
  );
};
