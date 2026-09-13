import { useId } from "react";
import type React from "react";

import { FieldMessage } from "./FormControls";
import { Icon } from "./icons/Icon";
import { Popover } from "./Popover";
import { SelectOptionPanel, useTriggerWidth } from "./Select";
import type { SelectLabels, SelectOption, SelectTriggerProps, SelectTriggerState } from "./Select";

/**
 * 이 부품이 화면에 내는 말 전부 (바퀴 11). 키는 `lib/labels` 의 `timeFieldLabel` 과 같은 모양이라
 * 부르는 쪽이 그것을 그대로 넘기면 된다. 부품은 언어를 모른다.
 */
export type TimeFieldLabels = {
  /** 값이 없을 때 트리거에 남는 글자 */
  placeholder: string;
  /** 목록 검색칸의 안내 글자(예: 14:30) */
  searchPlaceholder: string;
  /** 한 쌍 사이의 구분 기호 */
  dash: string;
  start: string;
  end: string;
  rangeInvalid: string;
};

/**
 * 시각 하나 · 시각 한 쌍.
 *
 * **네이티브 `input[type=time]` 을 쓰지 않는다.** 그것은 브라우저 로캘에 따라 같은 값을 「오후 2:30」
 * 으로도 "14:30" 으로도 그리고, 스피너 모양·너비·키보드가 플랫폼마다 다르다 — `DateField` 가
 * `input[type=date]` 를 화면에서 걷어낸 것과 정확히 같은 이유다. 여기서 보이는 글자는 우리가 만든
 * 24시간 `HH:MM` 이고, 값도 그 문자열 그대로 오간다.
 *
 * 고르기는 `Select` 와 같은 팝오버 목록을 그대로 쓴다 — 30분 간격이면 하루가 48칸이라, 검색·
 * ↑↓·타이핑 점프가 이미 붙어 있는 그 목록이 필요한 자리다. 트리거는 `DateField` 와 같은 h34 규격이라
 * 날짜와 시각이 한 줄에 나란히 선다.
 */

const MINUTES_IN_DAY = 1440;
/** 시각을 고르는 기본 간격. 회의·업무 시각에 1분 단위는 뜻이 없다. */
const DEFAULT_STEP = 30;
/** 시작을 옮겼는데 기존 간격을 알 수 없을 때 종료가 서는 자리. */
const DEFAULT_DURATION = 60;

/** 자정부터의 분 → `HH:MM`. 24시간 고정이라 로캘이 끼어들 자리가 없다. */
export function formatTime(minutes: number): string {
  const wrapped = ((minutes % MINUTES_IN_DAY) + MINUTES_IN_DAY) % MINUTES_IN_DAY;
  return `${String(Math.floor(wrapped / 60)).padStart(2, "0")}:${String(wrapped % 60).padStart(2, "0")}`;
}

/** `HH:MM` → 자정부터의 분. 시각이 아니면 null — "25:70" 같은 값은 여기서 걸린다. */
export function parseTime(value: string): number | null {
  const match = /^(\d{1,2}):(\d{2})$/.exec(value.trim());
  if (!match) return null;
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  if (hours > 23 || minutes > 59) return null;
  return hours * 60 + minutes;
}

/** `min`·`max` 는 ISO 날짜와 같은 이유로 문자열 비교로 충분하다 — `HH:MM` 은 사전순이 곧 시각순이다. */
export function timeSlots(step: number = DEFAULT_STEP, min?: string, max?: string): string[] {
  const slots: string[] = [];
  for (let minutes = 0; minutes < MINUTES_IN_DAY; minutes += step) {
    const slot = formatTime(minutes);
    if (min && slot < min) continue;
    if (max && slot > max) continue;
    slots.push(slot);
  }
  return slots;
}

function slotOptions(step: number, min: string | undefined, max: string | undefined, value: string): SelectOption[] {
  const slots = timeSlots(step, min, max);
  // 간격에 걸리지 않는 값(예: 기존 데이터의 14:17)도 목록에 세워 둔다 — 열자마자 사라지면 안 된다.
  if (value && !slots.includes(value) && parseTime(value) !== null) {
    slots.push(value);
    slots.sort();
  }
  return slots.map((slot) => ({ value: slot, label: slot }));
}

export function TimeField({
  id,
  label,
  value,
  onChange,
  step = DEFAULT_STEP,
  min,
  max,
  placeholder,
  disabled = false,
  error,
  invalid = false,
  help,
  trigger,
  labels,
  emptyActionLabel,
  selectLabels,
}: {
  /** 트리거 단추의 id — 호출부의 `.field` 레이블이 가리키는 자리 */
  id?: string;
  /** 접근 이름. 트리거와 목록이 같이 쓴다 */
  label: string;
  /** 24시간 `HH:MM`, 값이 없으면 빈 문자열 */
  value: string;
  onChange: (value: string) => void;
  /** 목록의 간격(분). 기본 30 */
  step?: number;
  /** 고를 수 있는 첫 시각 / 마지막 시각 (`HH:MM`) */
  min?: string;
  max?: string;
  placeholder?: string;
  disabled?: boolean;
  error?: string | null;
  /** 문장 없이 보더만 갈아입힌다 — 문장은 한 쌍 아래 한 번만 나간다 (TimeRangeField) */
  invalid?: boolean;
  help?: React.ReactNode;
  /** 트리거를 갈아 끼운다 — `Select` 와 같은 render prop */
  trigger?: (state: SelectTriggerState) => React.ReactNode;
  /** 이 부품이 내는 말 — `lib/labels` 의 `timeFieldLabel` 을 그대로 넘기면 된다 (바퀴 11) */
  labels: TimeFieldLabels;
  /** 목록의 「못 찾았다」에서 검색어를 비우는 단추 이름 */
  emptyActionLabel: string;
  /** 목록의 말 — `lib/labels` 의 `selectLabel` 을 그대로 넘기면 된다 */
  selectLabels: SelectLabels;
}) {
  const { ref, width } = useTriggerWidth();
  const messageId = useId();
  const options = slotOptions(step, min, max, value);
  const text = value || placeholder || labels.placeholder;
  const describedBy = error || help ? messageId : undefined;

  return (
    <>
      <Popover
        label={label}
        trigger={({ open, props }) => {
          const triggerProps: SelectTriggerProps = {
            ...props,
            "aria-describedby": describedBy,
            "aria-invalid": error || invalid ? true : undefined,
            "aria-label": label,
            disabled,
            id,
            ref: ref as React.Ref<HTMLButtonElement>,
          };
          if (trigger) return trigger({ open, label: text, props: triggerProps });
          return (
            <button {...triggerProps} className="select-trigger">
              <span className={value ? "select-value tabular" : "select-value placeholder"}>{text}</span>
              <Icon name="clock" size={16} />
            </button>
          );
        }}
        width={width}
      >
        {(close) => (
          <SelectOptionPanel
            close={close}
            label={label}
            onPick={onChange}
            options={options}
            emptyActionLabel={emptyActionLabel}
            labels={selectLabels}
            searchPlaceholder={labels.searchPlaceholder}
            selected={value ? [value] : []}
          />
        )}
      </Popover>
      <FieldMessage error={error} help={help} id={messageId} />
    </>
  );
}

export function TimeRangeField({
  id,
  label,
  start,
  end,
  onChange,
  step = DEFAULT_STEP,
  defaultDuration = DEFAULT_DURATION,
  min,
  max,
  disabled = false,
  error,
  help,
  startLabel,
  endLabel,
  labels,
  emptyActionLabel,
  selectLabels,
}: {
  /** 시작 트리거의 id — 호출부의 `.field` 레이블이 가리키는 자리 */
  id?: string;
  /** 한 쌍 전체의 이름. 각 트리거는 "<label> 시작 시각" 으로 읽힌다 */
  label: string;
  /** 24시간 `HH:MM` */
  start: string;
  end: string;
  onChange: (next: { start: string; end: string }) => void;
  step?: number;
  /** 간격을 알 수 없을 때 종료가 서는 자리(분). 기본 60 */
  defaultDuration?: number;
  min?: string;
  max?: string;
  disabled?: boolean;
  /** 주면 범위 검사보다 이 문장이 먼저다 */
  error?: string | null;
  help?: React.ReactNode;
  /** 한 쌍의 두 트리거 이름 — `lib/labels` 의 `timeFieldLabel.start`/`.end` (바퀴 11) */
  startLabel: string;
  endLabel: string;
  /** 이 부품이 내는 말 — `lib/labels` 의 `timeFieldLabel` 을 그대로 넘기면 된다 (바퀴 11) */
  labels: TimeFieldLabels;
  /** 목록의 「못 찾았다」에서 검색어를 비우는 단추 이름 */
  emptyActionLabel: string;
  /** 목록의 말 — `lib/labels` 의 `selectLabel` */
  selectLabels: SelectLabels;
}) {
  const messageId = useId();
  const startMinutes = parseTime(start);
  const endMinutes = parseTime(end);
  // 두 값이 다 시각일 때만 순서를 따진다 — 아직 비어 있는 것은 잘못된 것이 아니다.
  const invalid = startMinutes !== null && endMinutes !== null && endMinutes <= startMinutes;
  const message = error ?? (invalid ? labels.rangeInvalid : null);

  /** 시작을 옮기면 종료가 같은 간격만큼 따라간다 — 회의를 30분 당기는 일이 두 번의 조작이 되지 않게. */
  const moveStart = (next: string) => {
    const nextMinutes = parseTime(next);
    if (nextMinutes === null) {
      onChange({ start: next, end });
      return;
    }
    const held = startMinutes !== null && endMinutes !== null ? endMinutes - startMinutes : 0;
    const duration = held > 0 ? held : defaultDuration;
    onChange({ start: next, end: formatTime(nextMinutes + duration) });
  };

  return (
    <>
      <div className="time-range">
        <TimeField
          emptyActionLabel={emptyActionLabel}
          labels={labels}
          selectLabels={selectLabels}
          disabled={disabled}
          id={id}
          label={`${label} ${startLabel}`}
          max={max}
          min={min}
          onChange={moveStart}
          step={step}
          value={start}
        />
        <span aria-hidden className="time-range-dash">
          {labels.dash}
        </span>
        <TimeField
          emptyActionLabel={emptyActionLabel}
          labels={labels}
          selectLabels={selectLabels}
          disabled={disabled}
          invalid={Boolean(message)}
          label={`${label} ${endLabel}`}
          max={max}
          min={min}
          onChange={(next) => onChange({ start, end: next })}
          step={step}
          value={end}
        />
      </div>
      <FieldMessage error={message} help={help} id={messageId} />
    </>
  );
}
