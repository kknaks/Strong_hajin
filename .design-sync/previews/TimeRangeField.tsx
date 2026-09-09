import { DateField, TimeRangeField } from "ax-workspace-frontend";
import { useState } from "react";

/**
 * Storybook 메타. design-sync 컨버터는 대문자로 시작하는 named export 만 카드 셀로 세므로
 * (`lib/emit.mjs` 의 `/^[A-Z]/` 필터) 이 default export 는 프리뷰 카드에 영향을 주지 않는다.
 * 스토리를 더하려면 이 파일에 named export 를 하나 더 쓰면 된다 — 두 곳에 같이 반영된다.
 */
export default { title: "General/TimeRangeField", component: TimeRangeField };


/** 기본 — 시작을 옮기면 종료가 같은 간격만큼 따라간다 (defaultDuration 60) */
export const Range = () => {
  const [v, setV] = useState({ start: "14:00", end: "15:00" });
  return (
    <div className="field" style={{ width: 380, padding: 4 }}>
      <span>회의 시간</span>
      <TimeRangeField
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
    <TimeRangeField end="13:00" label="회의 시간" onChange={() => {}} start="14:00" />
  </div>
);

/** 날짜 + 시각 한 쌍 — 세 필드가 다 h34 라 한 줄에 선다 */
export const WithDate = () => {
  const [date, setDate] = useState("2026-09-08");
  const [time, setTime] = useState({ start: "10:00", end: "11:30" });
  return (
    <div className="field" style={{ width: 500, padding: 4 }}>
      <span>회의 일시</span>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <div style={{ width: 150 }}>
          <DateField hideLabel id="trf-date" label="날짜" onChange={setDate} value={date} />
        </div>
        <div style={{ flex: 1 }}>
          <TimeRangeField end={time.end} label="회의 시간" onChange={setTime} start={time.start} />
        </div>
      </div>
    </div>
  );
};

/** 비활성 — 한 쌍이 함께 잠긴다 */
export const Disabled = () => (
  <div className="field" style={{ width: 380, padding: 4 }}>
    <span>확정된 시간</span>
    <TimeRangeField disabled end="16:00" label="확정된 시간" onChange={() => {}} start="15:00" />
  </div>
);
