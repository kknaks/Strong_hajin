import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AssistantCharacterPicker } from "./AssistantCharacterPicker";


describe("AssistantCharacterPicker", () => {
  afterEach(cleanup);

  it("shows all nine approved catalog choices and selects one accessible option", () => {
    const onSelect = vi.fn();
    render(
      <AssistantCharacterPicker
        busy={false}
        currentKey="cream-cat"
        onClose={vi.fn()}
        onSelect={onSelect}
      />,
    );

    const dialog = screen.getByRole("dialog", { name: "내 AX 캐릭터" });
    expect(within(dialog).getAllByRole("radio")).toHaveLength(9);
    expect(within(dialog).getByRole("radio", { name: /크림 고양이/ }).getAttribute("aria-checked")).toBe("true");

    fireEvent.click(within(dialog).getByRole("radio", { name: /레서판다/ }));
    expect(onSelect).toHaveBeenCalledWith("red-panda");
  });
});
