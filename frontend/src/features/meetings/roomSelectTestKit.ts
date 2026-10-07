import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { expect } from "vitest";

/*
 * 회의실 셀렉트(`RoomSelect` — DS `Select` 드롭다운 · 2루프 E-3)를 시험에서 다루는 손잡이.
 * 예전에는 라디오 카드라 `getByLabelText(방 이름)` 로 바로 짚었다. 이제 목록은 트리거를 눌러야 열리고(포털 — `document.body`),
 * 고른 값은 트리거 글자로 읽는다. 네 자리(생성·수정 모달 · AX 생성·수정 카드)의 시험이 이 손잡이를 같이 쓴다.
 *
 * ⚠ **`waitFor` 콜백 안에서 목록을 열고 닫지 않는다.** `waitFor` 는 DOM 이 바뀔 때마다 콜백을 다시 부른다(MutationObserver) —
 * 콜백이 DOM 을 바꾸면 그 자체가 다시 부르는 신호가 돼 **끝없이 돌고 타이머가 영영 돌지 않는다**(시험 시간 제한도 안 걸린다).
 * 그래서 «기다리기» 는 닫혀 있을 때만 열고(열린 뒤에는 읽기만) 다 기다린 뒤 닫는다.
 */

/** 셀렉트 트리거 — 접근 이름은 「장소」(`meetingScreen.place`). `scope` 로 한 화면에 둘이 서도 가른다. */
export function roomTrigger(scope: HTMLElement = document.body): HTMLButtonElement {
  return within(scope).getByRole("button", { name: "장소" }) as HTMLButtonElement;
}

/** 지금 골라진 줄의 글자(트리거에 선 글자). 아무것도 안 골랐으면 안내 글자. DOM 을 바꾸지 않는다 — `waitFor` 안에서 써도 된다. */
export function roomValue(scope: HTMLElement = document.body): string {
  return roomTrigger(scope).querySelector(".select-value")?.textContent ?? "";
}

export type RoomOptionRow = { label: string; description: string | null; disabled: boolean; selected: boolean };

function labelOf(option: Element): string {
  return (option.querySelector(".select-option-text")?.firstChild?.textContent ?? option.textContent ?? "").trim();
}

function readOptions(): RoomOptionRow[] {
  return screen.queryAllByRole("option").map((option) => ({
    label: labelOf(option),
    description: option.querySelector(".select-option-text small")?.textContent ?? null,
    disabled: (option as HTMLButtonElement).disabled,
    selected: option.getAttribute("aria-selected") === "true",
  }));
}

function readGroups(): string[] {
  return [...document.querySelectorAll(".select-group")].map((node) => node.textContent ?? "");
}

function isOpen(trigger: HTMLButtonElement): boolean {
  return trigger.getAttribute("aria-expanded") === "true";
}

function open(scope: HTMLElement): boolean {
  const trigger = roomTrigger(scope);
  if (isOpen(trigger)) return false;
  fireEvent.click(trigger);
  return true;
}

function close(scope: HTMLElement) {
  const trigger = roomTrigger(scope);
  if (isOpen(trigger)) fireEvent.click(trigger);
}

/** 목록을 열어 줄을 읽고 다시 닫는다(동기 — **`waitFor` 안에서 부르지 않는다**). 묶음 머리(구분 — 「다른 회의실」)도 함께. */
export function roomOptions(scope: HTMLElement = document.body): { rows: RoomOptionRow[]; groups: string[] } {
  const opened = open(scope);
  const result = { rows: readOptions(), groups: readGroups() };
  if (opened) close(scope);
  return result;
}

/** 목록을 연 채 `check` 가 참이 될 때까지 기다린 뒤 닫는다 — 조회가 비동기라 줄이 늦게 선다. */
export async function waitForRows(scope: HTMLElement, check: (rows: RoomOptionRow[]) => boolean): Promise<RoomOptionRow[]> {
  let opened = false;
  let rows: RoomOptionRow[] = [];
  try {
    await waitFor(() => {
      /* 닫혀 있을 때만 연다 — 트리거가 잠깐 막혀(저장 중) 첫 클릭이 먹지 않았을 수 있다. 열린 뒤에는 DOM 을 건드리지 않는다 */
      opened = open(scope) || opened;
      rows = readOptions();
      expect(check(rows)).toBe(true);
    });
  } finally {
    if (opened) close(scope);
  }
  return rows;
}

/** 그 줄이 목록에 설 때까지 기다린다. */
export async function waitForRoom(scope: HTMLElement, label: string): Promise<RoomOptionRow> {
  const rows = await waitForRows(scope, (current) => current.some((row) => row.label === label));
  return rows.find((row) => row.label === label)!;
}

/** 줄 글자로 고른다 — 목록이 늦게 오면 그 줄이 설 때까지 기다린다. */
export async function pickRoom(scope: HTMLElement, label: string): Promise<void> {
  await waitFor(() => {
    open(scope);
    expect(readOptions().some((row) => row.label === label)).toBe(true);
  });
  const option = screen.getAllByRole("option").find((node) => labelOf(node) === label);
  if (!option) throw new Error(`회의실 줄이 없다: ${label}`);
  fireEvent.click(option);
  close(scope);
}
