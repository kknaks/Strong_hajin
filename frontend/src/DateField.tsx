import { useEffect, useId, useRef, useState } from "react";

import { formatDate } from "./labels";
import { Icon, type IconName } from "./Icon";

/**
 * A date control that defaults to YYYY/MM/DD and can adopt a screen-specific separator.
 *
 * `input[type=date]` renders in the browser's own locale, so the same field shows mm/dd/yyyy to one person and
 * dd.mm.yyyy to another. The visible field here is text we format ourselves; a native date input stays mounted,
 * visually hidden, purely to open the platform calendar through `showPicker()`. The value crossing `onChange` is
 * always ISO `YYYY-MM-DD` (or an empty string), so the API and database boundary is unchanged.
 */

const ISO = /^(\d{4})-(\d{2})-(\d{2})$/;

function formatDisplayDate(value: string, separator: "/" | "."): string {
  return formatDate(value).replaceAll("/", separator);
}

/** Accepts what people actually type: 2026/09/30, 2026-09-30, 2026.09.30, 20260930. */
export function parseDateInput(text: string): string | null {
  const digits = text.replace(/[^\d]/g, "");
  if (digits.length !== 8) return null;
  const iso = `${digits.slice(0, 4)}-${digits.slice(4, 6)}-${digits.slice(6, 8)}`;
  const match = ISO.exec(iso);
  if (!match) return null;
  const [, year, month, day] = match;
  const date = new Date(Date.UTC(Number(year), Number(month) - 1, Number(day)));
  // Reject a date the calendar does not have, such as 2026-02-31.
  const round = date.toISOString().slice(0, 10);
  return round === iso ? iso : null;
}

export function DateField({
  id,
  label,
  value,
  onChange,
  disabled = false,
  hideLabel = false,
  displaySeparator = "/",
  pickerIcon = "calendar",
  required = false,
}: {
  id: string;
  label: string;
  /** ISO `YYYY-MM-DD`, or an empty string for no date. */
  value: string;
  onChange: (isoValue: string) => void;
  disabled?: boolean;
  hideLabel?: boolean;
  displaySeparator?: "/" | ".";
  pickerIcon?: Extract<IconName, "calendar" | "chevron-down">;
  required?: boolean;
}) {
  const [text, setText] = useState(() => (value ? formatDisplayDate(value, displaySeparator) : ""));
  const pickerId = useId();
  const picker = useRef<HTMLInputElement>(null);

  // The field follows the value it is given, except while the person is mid-edit with an unparseable string.
  useEffect(() => {
    setText(value ? formatDisplayDate(value, displaySeparator) : "");
  }, [displaySeparator, value]);

  const commit = (next: string) => {
    setText(next);
    const trimmed = next.trim();
    if (!trimmed) {
      onChange("");
      return;
    }
    const parsed = parseDateInput(trimmed);
    if (parsed) onChange(parsed);
  };

  return (
    <div className="date-field">
      <label className={hideLabel ? "sr-only" : undefined} htmlFor={id}>
        {label}{required && <span aria-hidden className="danger-text"> *</span>}
      </label>
      <div className="date-field-control">
        <input
          aria-label={label}
          aria-describedby={`${pickerId}-hint`}
          autoComplete="off"
          disabled={disabled}
          id={id}
          inputMode="numeric"
          onBlur={() => setText(value ? formatDisplayDate(value, displaySeparator) : "")}
          onChange={(event) => commit(event.target.value)}
          placeholder={`YYYY${displaySeparator}MM${displaySeparator}DD`}
          required={required}
          type="text"
          value={text}
        />
        <button
          aria-label={`${label} 달력 열기`}
          className="btn h30 ghost date-field-picker"
          disabled={disabled}
          onClick={() => {
            const element = picker.current;
            if (!element) return;
            // showPicker keeps the platform calendar, its keyboard support and its screen-reader behaviour.
            if (typeof element.showPicker === "function") element.showPicker();
            else element.focus();
          }}
          type="button"
        >
          <Icon name={pickerIcon} size={14} />
        </button>
        <input
          aria-hidden
          className="sr-only"
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
          ref={picker}
          tabIndex={-1}
          type="date"
          value={value}
        />
      </div>
      <span className="sr-only" id={`${pickerId}-hint`}>
        연도 4자리, 월 2자리, 일 2자리 순서로 입력합니다. 예: 2026{displaySeparator}09{displaySeparator}30
      </span>
    </div>
  );
}
