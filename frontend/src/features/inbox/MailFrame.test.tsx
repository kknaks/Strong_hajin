import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { MailFrame } from "./MailFrame";

/* 메일 iframe 의 링크는 부모가 가로챈다(SPEC-008 §2.1 ⑦ · 검수 W-6·W-7③) — 열 수 있는 스킴만(F-1). */

let open: ReturnType<typeof vi.fn>;
beforeEach(() => {
  open = vi.fn();
  vi.stubGlobal("open", open);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function mount() {
  render(<MailFrame html="<p>본문</p>" messageId="m1" />);
  const frame = screen.getByTitle("메일 본문") as HTMLIFrameElement;
  const doc = frame.contentDocument!;
  doc.body.innerHTML = '<a id="ok" href="https://noeul.example/a?t=1">일정</a><a id="bad" href="javascript:alert(1)">보기</a>';
  fireEvent.load(frame);
  return doc;
}

it("클릭·가운데 클릭 모두 부모가 열고(새 탭 noopener,noreferrer) iframe 안에서는 이동하지 않는다", async () => {
  const doc = mount();
  const click = new MouseEvent("click", { bubbles: true, cancelable: true });
  doc.getElementById("ok")!.dispatchEvent(click);
  expect(click.defaultPrevented).toBe(true);
  await vi.waitFor(() => expect(open).toHaveBeenCalledWith("https://noeul.example/a?t=1", "_blank", "noopener,noreferrer"));
  const middle = new MouseEvent("auxclick", { bubbles: true, cancelable: true, button: 1 });
  doc.getElementById("ok")!.dispatchEvent(middle);
  expect(middle.defaultPrevented).toBe(true);
  await vi.waitFor(() => expect(open).toHaveBeenCalledTimes(2));
});

it("javascript: 링크는 막기만 하고 열지 않는다", async () => {
  const doc = mount();
  const click = new MouseEvent("click", { bubbles: true, cancelable: true });
  doc.getElementById("bad")!.dispatchEvent(click);
  expect(click.defaultPrevented).toBe(true);
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(open).not.toHaveBeenCalled();
});
