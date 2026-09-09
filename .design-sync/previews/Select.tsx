import { Icon, Select } from "ax-workspace-frontend";
import { useEffect, useRef, useState } from "react";

/**
 * Storybook 메타. design-sync 컨버터는 대문자로 시작하는 named export 만 카드 셀로 세므로
 * (`lib/emit.mjs` 의 `/^[A-Z]/` 필터) 이 default export 는 프리뷰 카드에 영향을 주지 않는다.
 * 스토리를 더하려면 이 파일에 named export 를 하나 더 쓰면 된다 — 두 곳에 같이 반영된다.
 */
export default { title: "General/Select", component: Select };


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

/** 디자인 시스템 카드용: 마운트 직후 트리거를 눌러 열고, 닫히면 다시 연다 — 카드에서만 쓴다. */
function Opened({ children, minHeight = 360 }: { children: React.ReactNode; minHeight?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const open = () => {
      const root = ref.current;
      if (!root || root.querySelector(".popover")) return;
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
        <Select id="sel-owner" label="담당자" onChange={setV} options={OWNERS} value={v} />
      </div>
      <div className="field">
        <span>참고 업무</span>
        <Select label="참고 업무" onChange={() => {}} options={OWNERS} placeholder="업무 고르기" value="" />
      </div>
      <div className="field">
        <span>배정자</span>
        <Select disabled label="배정자" onChange={() => {}} options={OWNERS} placeholder="고를 수 있는 사람이 없습니다" value="" />
      </div>
    </div>
  );
};

/** 에러 — 보더가 갈아입고 헬퍼 대신 한 줄이 나간다 (FieldMessage) */
export const Invalid = () => (
  <div className="field" style={{ width: 300, padding: 4 }}>
    <span>담당자</span>
    <Select error="담당자를 고르세요" label="담당자" onChange={() => {}} options={OWNERS} placeholder="담당자 고르기" value="" />
  </div>
);

/** 툴바 트리거 — trigger render prop 으로 filter-chip 을 끼운다 */
export const ChipTrigger = () => {
  const [v, setV] = useState("new");
  return (
    <div className="toolbar-group" style={{ padding: 4 }}>
      <Select
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
