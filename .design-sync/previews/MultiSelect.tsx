import { MultiSelect } from "ax-workspace-frontend";
import { useEffect, useRef, useState } from "react";

export default { title: "General/MultiSelect", component: MultiSelect };

/* 바퀴 11: 부품은 언어를 모른다 — `labels`·`emptyActionLabel` 이 필수다.
   키와 값은 `src/lib/labels.ts` 의 `selectLabel` 과 같다. */
const words = {
  labels: {
    placeholder: "선택",
    search: "검색",
    noMatch: "조건에 맞는 항목이 없습니다",
    clear: "지우기",
    selectAll: "전체 선택",
    clearAll: "전체 해제",
  },
  emptyActionLabel: "검색어 지우기",
};

const PEOPLE = [
  { value: "kim", label: "김서연", description: "플랫폼" },
  { value: "lee", label: "이준호", description: "데이터" },
  { value: "park", label: "박민지", description: "디자인" },
  { value: "choi", label: "최우식", description: "기획" },
  { value: "jung", label: "정하늘", description: "QA" },
];

/** 디자인 시스템 카드용: 마운트 직후 트리거를 눌러 열고, 닫히면 다시 연다 — 카드에서만 쓴다. */
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

/** 열린 상태 — 전체 선택/해제 · 옵션 체크는 Checkbox 와 같은 네모 */
export const Open = () => {
  const [v, setV] = useState<string[]>(["kim", "lee", "park"]);
  return (
    <Opened>
      <div className="field" style={{ width: 380 }}>
        <span>
          참석자 <span className="count-badge quiet">{v.length}</span>
        </span>
        <MultiSelect {...words} id="ms-attendees" label="참석자" onChange={setV} options={PEOPLE} placeholder="참석자 선택" value={v} />
      </div>
    </Opened>
  );
};

/** 닫힌 상태 — 칩 두 개까지 서고 나머지는 +N, 값이 있으면 지우기가 붙는다 */
export const Chips = () => {
  const [few, setFew] = useState<string[]>(["kim"]);
  const [many, setMany] = useState<string[]>(["kim", "lee", "park", "choi"]);
  const [none, setNone] = useState<string[]>([]);
  return (
    <div style={{ display: "grid", gap: 16, width: 360, padding: 4 }}>
      <div className="field">
        <span>한 명</span>
        <MultiSelect {...words} label="한 명" onChange={setFew} options={PEOPLE} placeholder="참석자 선택" value={few} />
      </div>
      <div className="field">
        <span>네 명 — +2 로 접힌다</span>
        <MultiSelect {...words} label="네 명" onChange={setMany} options={PEOPLE} placeholder="참석자 선택" value={many} />
      </div>
      <div className="field">
        <span>비어 있음</span>
        <MultiSelect {...words} label="비어 있음" onChange={setNone} options={PEOPLE} placeholder="참석자 선택" value={none} />
      </div>
    </div>
  );
};

/** 헬퍼 한 줄 — 필드 아래 문장은 FieldMessage 가 낸다 */
export const WithHelp = () => {
  const [v, setV] = useState<string[]>(["lee", "jung"]);
  return (
    <div className="field" style={{ width: 360, padding: 4 }}>
      <span>참조자</span>
      <MultiSelect {...words} help="회의록이 이 사람들에게도 간다" label="참조자" onChange={setV} options={PEOPLE} placeholder="참조자 선택" value={v} />
    </div>
  );
};
