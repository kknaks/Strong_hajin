import { DatePicker } from "./DatePicker";
import { Icon, type IconName } from "./Icon";

/**
 * 날짜를 받는 칸 — 전 화면이 **같은 달력** 하나를 쓴다 (DS-17).
 *
 * 예전에는 운영체제 달력(`input[type=date]` + `showPicker`)이 기본이었다. 그 달력은 브라우저마다 모양도
 * 글자 순서도 달라서, 같은 칸이 사람에 따라 mm/dd/yyyy 로도 dd.mm.yyyy 로도 보였다 — 우리가 그린 화면
 * 한가운데에 크롬 달력이 뜨는 것도 그 때문이다. 이제 갈래를 두지 않고 이 앱의 `DatePicker` 팝오버만 연다.
 *
 * 칸은 **하나**다: 값(`YYYY-MM-DD`)이 왼쪽, 달력 아이콘이 그 칸 «안» 오른쪽에 서고 칸 어디를 눌러도
 * 달력이 열린다 — 옆에 서는 시각 칸(`TimeField`)과 같은 자리·같은 크기다.
 *
 * 화면마다 구분자와 아이콘은 갈아 끼울 수 있다 (AX 카드는 `2026.09.30` + 「▾」로 낸다). 값 자체는
 * 언제나 ISO `YYYY-MM-DD`(또는 빈 문자열)로 오가므로 API·DB 경계는 그대로다.
 */
export function DateField({
  id,
  label,
  value,
  onChange,
  disabled = false,
  hideLabel = false,
  displaySeparator,
  pickerIcon = "calendar",
  required = false,
}: {
  id: string;
  label: string;
  /** ISO `YYYY-MM-DD`, 날짜가 없으면 빈 문자열. */
  value: string;
  onChange: (isoValue: string) => void;
  disabled?: boolean;
  hideLabel?: boolean;
  /** 화면에 낼 때의 구분자. 주지 않으면 ISO 그대로(`2026-09-30`)다. */
  displaySeparator?: "/" | ".";
  pickerIcon?: Extract<IconName, "calendar" | "chevron-down">;
  required?: boolean;
}) {
  const shown = displaySeparator ? value.replaceAll("-", displaySeparator) : value;
  const blank = displaySeparator ? `YYYY${displaySeparator}MM${displaySeparator}DD` : "YYYY-MM-DD";
  return (
    <div className="date-field">
      <label className={hideLabel ? "sr-only" : undefined} htmlFor={id}>
        {label}{required && <span aria-hidden className="danger-text"> *</span>}
      </label>
      <DatePicker
        id={id}
        label={label}
        onChange={onChange}
        trigger={({ props }) => (
          <button {...props} className="select-trigger" disabled={disabled}>
            <span className={value ? "select-value tabular" : "select-value placeholder"}>{shown || blank}</span>
            <Icon name={pickerIcon} size={16} />
          </button>
        )}
        value={value}
      />
    </div>
  );
}
