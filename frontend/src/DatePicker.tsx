import { useEffect, useRef, useState } from "react";
import type React from "react";

import { addDays, datePickerLabel, formatDate, formatMonthLong, seoulToday, weekdayNames } from "./labels";
import { Icon } from "./Icon";
import { Popover } from "./Popover";

/**
 * 날짜를 고르는 자리.
 *
 * 디자인 시스템 v2 는 달력 피커를 정의하지 않는다. 그래서 새 오버레이를 만들지 않고 **`14 — OVERLAY` 의
 * Popover 위에 얹는다** — 고르기는 팝오버라는 규칙(`16-4`)이 그대로 적용되는 자리라, 폭·띄움·그림자·
 * 닫는 길은 전부 `Popover` 가 이미 정해 둔 것을 쓴다. 이 파일이 더하는 것은 격자 하나뿐이다.
 *
 * 색도 새로 만들지 않는다. 고른 날은 `--text-primary` 배경 + `--on-fill` 글자 — v2 `16-3` 의 "검정은
 * 위치"이며, 캘린더(`13`)의 오늘 셀과 같은 규칙이다. 오늘은 테두리만으로 표시해 둘이 겹쳐도 읽힌다.
 *
 * 값은 언제나 ISO `YYYY-MM-DD`(또는 빈 문자열)로 오간다. 화면 글자는 `formatDate` 가 만든다.
 */

/** 달을 옮긴다. 31일에서 넘어갈 때 다음 달로 흘러넘치지 않게 그 달의 마지막 날로 자른다. */
function shiftMonth(isoDate: string, delta: number): string {
  const [year, month, day] = isoDate.split("-").map(Number);
  const target = new Date(Date.UTC(year, month - 1 + delta, 1));
  const lastDay = new Date(Date.UTC(target.getUTCFullYear(), target.getUTCMonth() + 1, 0)).getUTCDate();
  return new Date(Date.UTC(target.getUTCFullYear(), target.getUTCMonth(), Math.min(day, lastDay))).toISOString().slice(0, 10);
}

/** ISO 는 사전순이 곧 날짜순이라 경계 비교에 파싱이 필요 없다. */
function clamp(isoDate: string, min?: string, max?: string): string {
  if (min && isoDate < min) return min;
  if (max && isoDate > max) return max;
  return isoDate;
}

function DatePickerPanel({
  value,
  onChange,
  min,
  max,
  close,
}: {
  value: string;
  onChange: (isoValue: string) => void;
  min?: string;
  max?: string;
  close: () => void;
}) {
  const today = seoulToday();
  const [focus, setFocus] = useState(() => clamp(value || today, min, max));
  const gridRef = useRef<HTMLDivElement>(null);
  // 열릴 때와 키보드로 옮길 때만 눈을 옮긴다. 달 넘기기 단추를 마우스로 눌렀을 때까지 격자로 끌고 가면
  // 단추를 연달아 누를 수 없다.
  const moveFocus = useRef(true);

  useEffect(() => {
    if (!moveFocus.current) return;
    gridRef.current?.querySelector<HTMLButtonElement>(`[data-date="${focus}"]`)?.focus();
  }, [focus]);

  const goto = (isoDate: string, withFocus: boolean) => {
    moveFocus.current = withFocus;
    setFocus(clamp(isoDate, min, max));
  };

  const cursor = focus.slice(0, 7);
  const [year, month] = cursor.split("-").map(Number);
  const gridStart = addDays(`${cursor}-01`, -new Date(Date.UTC(year, month - 1, 1)).getUTCDay());
  const days = Array.from({ length: 42 }, (_, index) => addDays(gridStart, index));
  const weeks = Array.from({ length: 6 }, (_, index) => days.slice(index * 7, index * 7 + 7));

  const outOfRange = (isoDate: string) => Boolean((min && isoDate < min) || (max && isoDate > max));
  const pick = (isoDate: string) => {
    onChange(isoDate);
    close();
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const step = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 }[event.key];
    if (step) {
      event.preventDefault();
      goto(addDays(focus, step), true);
      return;
    }
    // 단추라 브라우저가 알아서 눌러 주지만, 여기서 먼저 받아 두 경로가 갈리지 않게 한다.
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      if (!outOfRange(focus)) pick(focus);
    }
  };

  return (
    <>
      <div className="date-picker-head">
        <button
          aria-label={datePickerLabel.previousMonth}
          className="btn h30 icon ghost date-picker-prev"
          onClick={() => goto(shiftMonth(focus, -1), false)}
          type="button"
        >
          <Icon name="chevron-right" size={12} />
        </button>
        <span className="t-item">{formatMonthLong(year, month)}</span>
        <button
          aria-label={datePickerLabel.nextMonth}
          className="btn h30 icon ghost"
          onClick={() => goto(shiftMonth(focus, 1), false)}
          type="button"
        >
          <Icon name="chevron-right" size={12} />
        </button>
      </div>
      <div aria-hidden className="calendar-weekdays">
        {weekdayNames.map((weekday) => (
          <span key={weekday}>{weekday}</span>
        ))}
      </div>
      <div className="date-picker-grid" onKeyDown={onKeyDown} ref={gridRef} role="grid">
        {weeks.map((week) => (
          <div className="date-picker-row" key={week[0]} role="row">
            {week.map((day) => (
              <button
                aria-current={day === today ? "date" : undefined}
                aria-label={formatDate(day)}
                aria-selected={day === value}
                className={["date-picker-cell", day.startsWith(cursor) ? "" : "outside", day === today ? "today" : ""].filter(Boolean).join(" ")}
                data-date={day}
                disabled={outOfRange(day)}
                key={day}
                onClick={() => pick(day)}
                role="gridcell"
                tabIndex={day === focus ? 0 : -1}
                type="button"
              >
                {Number(day.slice(8))}
              </button>
            ))}
          </div>
        ))}
      </div>
      <div className="date-picker-foot">
        <button className="btn link" onClick={() => pick("")} type="button">
          {datePickerLabel.clear}
        </button>
        <button className="btn h30" disabled={outOfRange(today)} onClick={() => pick(today)} type="button">
          {datePickerLabel.today}
        </button>
      </div>
    </>
  );
}

export function DatePicker({
  value,
  onChange,
  min,
  max,
  id,
  label,
}: {
  /** ISO `YYYY-MM-DD`, 날짜가 없으면 빈 문자열. */
  value: string;
  /** 날짜를 고르거나 지울 때. 지우면 빈 문자열이 온다. */
  onChange: (isoValue: string) => void;
  /** 고를 수 있는 첫 날 / 마지막 날 (ISO). */
  min?: string;
  max?: string;
  id?: string;
  /** 패널 이름. 트리거는 여기에 "달력 열기"를 붙여 읽는다. */
  label: string;
}) {
  return (
    <Popover
      label={label}
      trigger={({ props }) => (
        <button {...props} aria-label={`${label} ${datePickerLabel.open}`} className="btn h30 ghost date-picker-trigger" id={id}>
          <Icon name="calendar" size={14} />
        </button>
      )}
      width={280}
    >
      {(close) => <DatePickerPanel close={close} max={max} min={min} onChange={onChange} value={value} />}
    </Popover>
  );
}
