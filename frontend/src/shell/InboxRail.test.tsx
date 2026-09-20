import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { InboxRail } from "./InboxRail";
import { requestInboxFixture } from "./fixtures/requestInbox";

afterEach(cleanup);
it("offers direct decisions only for open work requests and keeps card titles keyboard accessible", () => {
  const onOpen = vi.fn(), onAccept = vi.fn(), onReject = vi.fn();
  render(<InboxRail items={requestInboxFixture} state="ready" personas={[]} canDecide onOpen={onOpen} onAccept={onAccept} onReject={onReject} onRetry={vi.fn()} />);
  const card = within(screen.getByRole("button", { name: requestInboxFixture[0].title }).closest("article")!);
  fireEvent.click(card.getByRole("button", { name: "수락" }));
  expect(onAccept).toHaveBeenCalledWith(requestInboxFixture[0]);
  expect(onOpen).not.toHaveBeenCalled();
  fireEvent.click(card.getByRole("button", { name: "거절" }));
  expect(onReject).toHaveBeenCalledWith(requestInboxFixture[0]);
  fireEvent.click(screen.getByRole("tab", { name: "참고" }));
  expect(screen.queryByRole("button", { name: "수락" })).toBeNull();
  expect(screen.queryByRole("button", { name: "거절" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: requestInboxFixture[2].title }));
  expect(onOpen).toHaveBeenCalledWith(requestInboxFixture[2]);
});
it("does not expose decisions without permission and disables them while a command is pending", () => {
  const props = { items: requestInboxFixture, state: "ready" as const, personas: [], onOpen: vi.fn(), onRetry: vi.fn(), onAccept: vi.fn(), onReject: vi.fn() };
  const { rerender } = render(<InboxRail {...props} />);
  expect(screen.queryByRole("button", { name: "수락" })).toBeNull();
  rerender(<InboxRail {...props} canDecide busy />);
  for (const button of screen.getAllByRole("button", { name: /^(수락|거절)$/ })) expect(button.hasAttribute("disabled")).toBe(true);
});
