import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { Button } from "./Button";
import type React from "react";

import { Badge } from "./Badge";
import { CheckboxBox, FieldMessage } from "./FormControls";
import { Empty } from "./Empty";
import { Icon } from "./icons/Icon";
import { Popover } from "./Popover";

/**
 * 하나 고르는 자리 · 여럿 고르는 자리.
 *
 * **네이티브 `<select>` 를 쓰지 않는다.** 네이티브 목록은 브라우저가 OS 위젯으로 그려서 우리 토큰이
 * 하나도 닿지 않는다 — 같은 화면에서 그 필드만 폰트·높이·라운드가 다르고, 그룹 헤더·설명 줄·검색·
 * 다중 선택 같은 것은 애초에 그릴 수가 없다. 그래서 디자인 시스템 v2 `14 — OVERLAY` 의 규칙
 * (「고르기는 팝오버」)을 그대로 따라 `Popover` 위에 목록을 얹는다. 새 오버레이를 만들지 않는다.
 *
 * 트리거는 폼 필드 규격 그대로다 — h34 · `--radius-control` · `--border-strong` · 14px (v2 `09`).
 * 툴바에서는 `trigger` 로 `filter-chip` 이나 `btn ghost` 를 끼운다 — `DatePicker` 가 `Popover` 에
 * 트리거를 넘기는 방식과 같다.
 *
 * 목록 안의 부품은 이미 있는 것을 쓴다: 검색은 `.search-input-box`, 항목은 `.popover-item`,
 * 다중 선택의 체크는 `Checkbox` 와 같은 `CheckboxBox`, 걸린 것이 없으면 `Empty variant="filter"`,
 * 필드 아래 한 줄은 `FieldMessage`.
 */

export type SelectOption = {
  value: string;
  label: string;
  /** 항목 아래 한 줄 — 같은 이름이 여럿일 때 무엇으로 갈리는지 */
  description?: string;
  disabled?: boolean;
  /** 같은 값이 이어지는 동안 한 번만 머리글이 뜬다. 정렬은 호출부가 한 순서 그대로다 */
  group?: string;
};

/** `Popover` 가 주는 aria + onClick 에 이 컴포넌트가 필드로서 아는 것(이름·비활성·id)을 얹은 것. */
export type SelectTriggerProps = React.ButtonHTMLAttributes<HTMLButtonElement> & { ref?: React.Ref<HTMLButtonElement> };

export type SelectTriggerState = {
  open: boolean;
  /** 지금 값이 화면에 읽히는 글자. 값이 없으면 placeholder */
  label: string;
  props: SelectTriggerProps;
};

/**
 * 이 부품이 화면에 내는 말 전부 (바퀴 11). 부품은 언어를 모른다 — 부르는 쪽이 `lib/labels` 에서
 * 가져다 넘긴다. 키는 거기 `selectLabel` 과 같은 모양이라 그대로 넘기면 된다.
 */
export type SelectLabels = {
  /** 값이 없을 때 트리거에 남는 글자 */
  placeholder: string;
  /** 검색칸의 이름이자 안내 글자 */
  search: string;
  /** 검색 결과가 없을 때의 제목 */
  noMatch: string;
  /** 고른 값을 비우는 자리 */
  clear: string;
  selectAll: string;
  clearAll: string;
};

export type SelectFooterAction = { label: string; onAction: () => void };

/** 이보다 많으면 눈으로 훑는 것보다 치는 것이 빠르다 — 검색창이 저절로 붙는 경계. */
const SEARCH_THRESHOLD = 8;
/** v2 14 의 팝오버 폭. 트리거에 맞추되 이 범위를 벗어나지 않는다. */
const MIN_WIDTH = 200;
const MAX_WIDTH = 400;
/** 타이핑 점프 — 이만큼 쉬면 다음 글자는 새 낱말로 친다. */
const JUMP_RESET_MS = 800;

function clampWidth(width: number): number {
  return Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, Math.round(width)));
}

/** 트리거 폭을 재서 목록 폭을 맞춘다. 재기 전 한 프레임은 최소 폭으로 선다. */
function useTriggerWidth() {
  const ref = useRef<HTMLElement | null>(null);
  const [width, setWidth] = useState(MIN_WIDTH);
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    const measure = () => setWidth(clampWidth(element.getBoundingClientRect().width));
    measure();
    // jsdom 에는 ResizeObserver 가 없다. 한 번 재는 것으로 충분한 환경이라 조용히 넘어간다.
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return { ref, width };
}

function OptionPanel({
  options,
  selected,
  onPick,
  multiple = false,
  searchable,
  searchPlaceholder,
  footerAction,
  onToggleAll,
  allSelected,
  label,
  labels,
  emptyActionLabel,
  close,
}: {
  options: ReadonlyArray<SelectOption>;
  /** 지금 골라져 있는 값들. Select 는 0~1개 */
  selected: ReadonlyArray<string>;
  onPick: (value: string) => void;
  multiple?: boolean;
  searchable?: boolean;
  searchPlaceholder?: string;
  footerAction?: SelectFooterAction;
  onToggleAll?: () => void;
  allSelected?: boolean;
  label: string;
  labels: SelectLabels;
  /** 「못 찾았다」 자리에서 검색어를 비우는 단추 이름 */
  emptyActionLabel: string;
  close: () => void;
}) {
  const [query, setQuery] = useState("");
  // 열릴 때 눈은 지금 값에 선다 — 48칸짜리 시각 목록에서 이것이 없으면 늘 자정부터 찾아야 한다.
  const [active, setActive] = useState(() => {
    const index = options.filter((option) => !option.disabled).findIndex((option) => selected.includes(option.value));
    return index < 0 ? 0 : index;
  });
  const listId = useId();
  const listRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const jump = useRef({ text: "", at: 0 });

  const showSearch = searchable ?? options.length > SEARCH_THRESHOLD;
  const needle = query.trim().toLowerCase();
  const matches = needle
    ? options.filter((option) => `${option.label} ${option.description ?? ""}`.toLowerCase().includes(needle))
    : options;
  // 키보드는 고를 수 있는 것만 지난다. 비활성 항목은 보이되 건너뛴다.
  const pickable = matches.filter((option) => !option.disabled);
  const activeIndex = Math.min(active, Math.max(0, pickable.length - 1));

  // 검색창이 있으면 글자는 거기로 가야 한다. 없을 때만 목록이 포커스를 받는다.
  useEffect(() => {
    (searchRef.current ?? listRef.current)?.focus();
  }, []);

  // 48칸짜리 시각 목록에서 지금 값이 스크롤 밖에 있으면 열어도 보이지 않는다.
  useEffect(() => {
    const row = listRef.current?.querySelector<HTMLElement>('[data-active="true"]');
    // jsdom 에는 scrollIntoView 가 없다 — 스크롤 위치는 테스트가 볼 것도 아니라 조용히 넘어간다.
    if (typeof row?.scrollIntoView === "function") row.scrollIntoView({ block: "nearest" });
  }, [activeIndex, query]);

  const pick = (option: SelectOption) => {
    if (option.disabled) return;
    onPick(option.value);
    // 여럿 고르는 중에는 닫지 않는다 — 하나 누를 때마다 닫히면 다섯 명을 고를 수가 없다.
    if (!multiple) close();
  };

  // Esc 는 Popover 가 받는다 — 열린 겹 중 맨 위 하나만 닫히므로 부모 Drawer 는 그대로다.
  // 여기서는 목록 안에서만 뜻이 있는 키를 본다.
  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const next = activeIndex + (event.key === "ArrowDown" ? 1 : -1);
      setActive(Math.min(pickable.length - 1, Math.max(0, next)));
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      const option = pickable[activeIndex];
      if (option) pick(option);
      return;
    }
    // 타이핑 점프는 검색창이 없을 때만 — 있으면 같은 글자가 두 가지 뜻을 갖는다.
    if (!showSearch && event.key.length === 1 && !event.altKey && !event.ctrlKey && !event.metaKey) {
      const now = Date.now();
      const text = (now - jump.current.at < JUMP_RESET_MS ? jump.current.text : "") + event.key.toLowerCase();
      jump.current = { text, at: now };
      const found = pickable.findIndex((option) => option.label.toLowerCase().startsWith(text));
      if (found >= 0) setActive(found);
    }
  };

  const rows: React.ReactNode[] = [];
  let group: string | undefined;
  let cursor = 0;
  for (const option of matches) {
    if (option.group && option.group !== group) {
      group = option.group;
      rows.push(
        <div className="select-group" key={`group-${option.group}`}>
          {option.group}
        </div>,
      );
    }
    const index = option.disabled ? -1 : cursor++;
    const isSelected = selected.includes(option.value);
    const isActive = index >= 0 && index === activeIndex;
    rows.push(
      <button
        aria-checked={isSelected}
        aria-selected={isSelected}
        className={["popover-item", option.description ? "stacked" : "", isActive ? "active" : ""].filter(Boolean).join(" ")}
        data-active={isActive || undefined}
        disabled={option.disabled}
        id={`${listId}-option-${index}`}
        key={option.value}
        onClick={() => pick(option)}
        onMouseEnter={() => index >= 0 && setActive(index)}
        role="option"
        tabIndex={-1}
        type="button"
      >
        {multiple ? (
          <CheckboxBox checked={isSelected} disabled={option.disabled} />
        ) : isSelected ? (
          <Icon name="check" size={14} />
        ) : (
          <span aria-hidden className="select-check" />
        )}
        <span className="select-option-text">
          {option.label}
          {option.description && <small>{option.description}</small>}
        </span>
      </button>,
    );
  }

  return (
    <>
      {showSearch && (
        <div className="popover-head select-search">
          <input
            aria-label={labels.search}
            autoComplete="off"
            className="search-input-box"
            onChange={(event) => {
              setQuery(event.target.value);
              setActive(0);
            }}
            onKeyDown={onKeyDown}
            placeholder={searchPlaceholder ?? labels.search}
            ref={searchRef}
            type="text"
            value={query}
          />
        </div>
      )}
      {onToggleAll && (
        <div className="select-all">
          <Button variant="inline" onClick={onToggleAll} type="button">
            {allSelected ? labels.clearAll : labels.selectAll}
          </Button>
        </div>
      )}
      <div
        aria-activedescendant={pickable.length > 0 ? `${listId}-option-${activeIndex}` : undefined}
        aria-label={label}
        aria-multiselectable={multiple || undefined}
        className="select-list"
        onKeyDown={onKeyDown}
        ref={listRef}
        role="listbox"
        tabIndex={-1}
      >
        {rows}
      </div>
      {matches.length === 0 && (
        <Empty actionLabel={emptyActionLabel} className="select-empty" onAction={() => setQuery("")} title={labels.noMatch} variant="filter" />
      )}
      {footerAction && (
        <div className="select-foot">
          <Button
            variant="inline"
            onClick={() => {
              footerAction.onAction();
              close();
            }}
            type="button"
          >
            {footerAction.label}
          </Button>
        </div>
      )}
    </>
  );
}

export function Select({
  id,
  label,
  value,
  onChange,
  options,
  placeholder,
  disabled = false,
  error,
  invalid = false,
  help,
  searchable,
  searchPlaceholder,
  footerAction,
  trigger,
  labels,
  emptyActionLabel,
}: {
  /** 트리거 단추의 id — 호출부의 `.field` 레이블이 가리키는 자리 */
  id?: string;
  /** 접근 이름. 트리거와 목록이 같이 쓴다 */
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: ReadonlyArray<SelectOption>;
  /** 값이 없을 때 트리거에 남는 글자 */
  placeholder?: string;
  disabled?: boolean;
  /** 있으면 보더가 갈아입고 헬퍼 대신 이 문장이 나간다 (v2 09) */
  error?: string | null;
  /** 문장 없이 보더만 갈아입힌다 — 문장이 한 쌍 아래 한 번만 나가는 자리(TimeRangeField)에서 쓴다 */
  invalid?: boolean;
  help?: React.ReactNode;
  /** 기본은 자동 — 옵션이 8개를 넘으면 검색창이 붙는다 */
  searchable?: boolean;
  searchPlaceholder?: string;
  /** 목록 바닥의 한 줄 행동 (글줄 속 단추) — 예: 「새 프로젝트로 추가」 */
  footerAction?: SelectFooterAction;
  /** 트리거를 갈아 끼운다 — 툴바의 `filter-chip`, 아이콘만 있는 `btn ghost` 등 */
  trigger?: (state: SelectTriggerState) => React.ReactNode;
  /** 이 부품이 내는 말 — `lib/labels` 의 `selectLabel` 을 그대로 넘기면 된다 (바퀴 11) */
  labels: SelectLabels;
  /** 검색 결과가 없을 때 검색어를 비우는 단추 이름 */
  emptyActionLabel: string;
}) {
  const { ref, width } = useTriggerWidth();
  const messageId = useId();
  const current = options.find((option) => option.value === value);
  const text = current?.label ?? placeholder ?? labels.placeholder;
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
              <span className={current ? "select-value" : "select-value placeholder"}>{text}</span>
              <Icon name="chevron-down" size={12} />
            </button>
          );
        }}
        width={width}
      >
        {(close) => (
          <OptionPanel
            close={close}
            emptyActionLabel={emptyActionLabel}
            footerAction={footerAction}
            label={label}
            labels={labels}
            onPick={onChange}
            options={options}
            searchPlaceholder={searchPlaceholder}
            searchable={searchable}
            selected={value ? [value] : []}
          />
        )}
      </Popover>
      <FieldMessage error={error} help={help} id={messageId} />
    </>
  );
}

export function MultiSelect({
  id,
  label,
  value,
  onChange,
  options,
  placeholder,
  disabled = false,
  error,
  invalid = false,
  help,
  searchable,
  searchPlaceholder,
  footerAction,
  maxChips = 2,
  selectAll = true,
  trigger,
  labels,
  emptyActionLabel,
}: {
  id?: string;
  label: string;
  value: ReadonlyArray<string>;
  onChange: (values: string[]) => void;
  options: ReadonlyArray<SelectOption>;
  placeholder?: string;
  disabled?: boolean;
  error?: string | null;
  invalid?: boolean;
  help?: React.ReactNode;
  searchable?: boolean;
  searchPlaceholder?: string;
  footerAction?: SelectFooterAction;
  /** 칩으로 세워 두는 개수. 넘으면 `+N` 으로 접는다 */
  maxChips?: number;
  /** 목록 머리의 전체 선택/해제 */
  selectAll?: boolean;
  trigger?: (state: SelectTriggerState) => React.ReactNode;
  /** 이 부품이 내는 말 — `lib/labels` 의 `selectLabel` 을 그대로 넘기면 된다 (바퀴 11) */
  labels: SelectLabels;
  /** 검색 결과가 없을 때 검색어를 비우는 단추 이름 */
  emptyActionLabel: string;
}) {
  const { ref, width } = useTriggerWidth();
  const messageId = useId();
  const picked = options.filter((option) => value.includes(option.value));
  const chips = picked.slice(0, maxChips);
  const overflow = picked.length - chips.length;
  const text = picked.length > 0 ? picked.map((option) => option.label).join(", ") : (placeholder ?? labels.placeholder);
  const describedBy = error || help ? messageId : undefined;
  const enabled = options.filter((option) => !option.disabled);
  const allSelected = enabled.length > 0 && enabled.every((option) => value.includes(option.value));

  const toggle = (next: string) => {
    onChange(value.includes(next) ? value.filter((item) => item !== next) : [...value, next]);
  };

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
          };
          if (trigger) return trigger({ open, label: text, props: { ...triggerProps, ref: ref as React.Ref<HTMLButtonElement> } });
          return (
            // 지우기가 여는 단추 안에 들어갈 수 없어서 트리거는 상자 하나에 단추 둘이다.
            // Popover 는 Esc 로 닫을 때 root 의 첫 단추로 눈을 돌리는데, 그것이 여는 단추다.
            <span className={disabled ? "select-trigger multi disabled" : "select-trigger multi"} ref={ref as React.Ref<HTMLSpanElement>}>
              <button {...triggerProps} className="select-open">
                {picked.length === 0 ? (
                  <span className="select-value placeholder">{placeholder ?? labels.placeholder}</span>
                ) : (
                  chips.map((option) => (
                    <Badge tone="accent" key={option.value}>
                      {option.label}
                    </Badge>
                  ))
                )}
                {overflow > 0 && <span className="t-meta">+{overflow}</span>}
              </button>
              {picked.length > 0 && !disabled && (
                <button aria-label={labels.clear} className="select-clear" onClick={() => onChange([])} type="button">
                  <Icon name="close" size={12} />
                </button>
              )}
              <Icon name="chevron-down" size={12} />
            </span>
          );
        }}
        width={width}
      >
        {(close) => (
          <OptionPanel
            allSelected={allSelected}
            close={close}
            emptyActionLabel={emptyActionLabel}
            footerAction={footerAction}
            label={label}
            labels={labels}
            multiple
            onPick={toggle}
            onToggleAll={selectAll ? () => onChange(allSelected ? [] : enabled.map((option) => option.value)) : undefined}
            options={options}
            searchPlaceholder={searchPlaceholder}
            searchable={searchable}
            selected={value}
          />
        )}
      </Popover>
      <FieldMessage error={error} help={help} id={messageId} />
    </>
  );
}

/** 시각 목록도 같은 팝오버를 쓴다 — `TimeField` 가 옵션만 만들어 넘긴다. */
export { OptionPanel as SelectOptionPanel, useTriggerWidth };
