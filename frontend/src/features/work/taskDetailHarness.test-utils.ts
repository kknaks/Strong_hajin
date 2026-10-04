import { fireEvent, screen } from "@testing-library/react";

/**
 * 업무 상세의 **명령 입구 둘**을 테스트에서 다루는 손잡이 (WORK-010 2b · SPEC-007 §2.10.1 · §2.10.5).
 *
 * 푸터가 없어졌다 — 상태 전이는 메타 정보의 **진행 상태 셀렉트**, 요청자의 제안은 머리의 **`⋯`** 다.
 * 예전 테스트가 「단추가 선다/없다」로 적던 계약을 「목록에 그 항목이 있다/없다」로 옮겨 적는 자리다.
 */

const STATE_SELECT = "진행 상태 바꾸기";
const PROPOSAL_MENU = "요청자 제안";

function optionNames(): string[] {
  return screen.getAllByRole("option").map((option) => option.textContent ?? "");
}

/** 진행 상태 셀렉트의 항목들(지금 상태 포함). **셀렉트가 없으면(상태 글자뿐) `null`** 이다. 열었다 닫는다. */
export async function stateOptions(): Promise<string[] | null> {
  await screen.findByLabelText("메타 정보");
  const trigger = screen.queryByRole("button", { name: STATE_SELECT });
  if (!trigger) return null;
  fireEvent.click(trigger);
  const names = optionNames();
  fireEvent.click(trigger);
  return names;
}

/** 진행 상태 셀렉트에서 한 항목을 고른다. */
export function chooseState(name: string) {
  fireEvent.click(screen.getByRole("button", { name: STATE_SELECT }));
  fireEvent.click(screen.getByRole("option", { name }));
}

/** 머리 `⋯` 의 항목들. **`⋯` 가 없으면 `null`** 이다. 열었다 닫는다. */
export async function proposalItems(): Promise<string[] | null> {
  await screen.findByLabelText("메타 정보");
  const trigger = screen.queryByRole("button", { name: PROPOSAL_MENU });
  if (!trigger) return null;
  fireEvent.click(trigger);
  const names = optionNames();
  fireEvent.click(trigger);
  return names;
}

/** 머리 `⋯` 에서 한 항목을 고른다. */
export function chooseProposal(name: string) {
  fireEvent.click(screen.getByRole("button", { name: PROPOSAL_MENU }));
  fireEvent.click(screen.getByRole("option", { name }));
}
