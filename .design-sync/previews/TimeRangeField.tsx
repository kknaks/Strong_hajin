import { DateField, TimeRangeField } from "ax-workspace-frontend";
import { useState } from "react";

export default { title: "General/TimeRangeField", component: TimeRangeField };

/* 바퀴 11: `src/ds/` 안에는 한국어가 0이다 — 화면에 나가는 말은 전부 prop 이다.
   이 부품은 다섯을 받는다: `labels` · `startLabel` · `endLabel` · `emptyActionLabel` · `selectLabels`.
   키와 값은 `src/lib/labels.ts` 의 `timeFieldLabel` · `selectLabel` 과 같다. */
const words = {
  labels: {
    placeholder: "시각 선택",
    searchPlaceholder: "14:30",
    dash: "–",
    start: "시작 시각",
    end: "종료 시각",
    rangeInvalid: "종료가 시작보다 빠릅니다",
  },
  startLabel: "시작 시각",
  endLabel: "종료 시각",
  emptyActionLabel: "검색어 지우기",
  selectLabels: {
    placeholder: "선택",
    search: "검색",
    noMatch: "조건에 맞는 항목이 없습니다",
    clear: "지우기",
    selectAll: "전체 선택",
    clearAll: "전체 해제",
  },
};

/* 옆에 세우는 `DateField` 도 같은 이유로 말과 시계를 받는다 */
const dateWords = {
  today: "2026-09-14",
  labels: { open: "달력 열기", previousMonth: "이전 달", nextMonth: "다음 달", clear: "지우기", today: "오늘" },
  weekdayNames: ["일", "월", "화", "수", "목", "금", "토"],
  // `lib/labels.ts` 의 `formatMonthLong` 과 같다 — month 는 **1-based** 다(+1 하지 않는다).
  formatMonth: (year: number, month: number) => `${year}년 ${month}월`,
};

/** 기본 — 시작을 옮기면 종료가 같은 간격만큼 따라간다 (defaultDuration 60) */
export const Range = () => {
  const [v, setV] = useState({ start: "14:00", end: "15:00" });
  return (
    <div className="field" style={{ width: 380, padding: 4 }}>
      <span>회의 시간</span>
      <TimeRangeField
        {...words}
        end={v.end}
        help="시작을 옮기면 종료가 같은 간격만큼 따라간다 · defaultDuration 60"
        id="trf-meeting"
        label="회의 시간"
        onChange={setV}
        start={v.start}
      />
    </div>
  );
};

/** 종료가 시작보다 빠를 때 — 한 줄로 말하고 종료 쪽 보더만 갈아입는다 */
export const Invalid = () => (
  <div className="field" style={{ width: 380, padding: 4 }}>
    <span>회의 시간</span>
    <TimeRangeField {...words} end="13:00" label="회의 시간" onChange={() => {}} start="14:00" />
  </div>
);

/**
 * 날짜 + 시각 한 쌍 — 세 필드가 다 h34 라 한 줄에 선다.
 * 카드 폭 함정: 560 으로 두면 오른쪽에서 종료 필드가 잘린다. 스토리 500 + 뷰포트 620x520 으로 맞춘다.
 */
export const WithDate = () => {
  const [date, setDate] = useState("2026-09-08");
  const [time, setTime] = useState({ start: "10:00", end: "11:30" });
  return (
    <div className="field" style={{ width: 500, padding: 4 }}>
      <span>회의 일시</span>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <div style={{ width: 150 }}>
          <DateField {...dateWords} hideLabel id="trf-date" label="날짜" onChange={setDate} value={date} />
        </div>
        <div style={{ flex: 1 }}>
          <TimeRangeField {...words} end={time.end} label="회의 시간" onChange={setTime} start={time.start} />
        </div>
      </div>
    </div>
  );
};

/** 비활성 — 한 쌍이 함께 잠긴다 */
export const Disabled = () => (
  <div className="field" style={{ width: 380, padding: 4 }}>
    <span>확정된 시간</span>
    <TimeRangeField {...words} disabled end="16:00" label="확정된 시간" onChange={() => {}} start="15:00" />
  </div>
);
