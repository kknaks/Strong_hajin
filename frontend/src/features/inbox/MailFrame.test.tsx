import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { MAIL_SANDBOX, MailFrame } from "./MailFrame";

/*
 * 메일 iframe (SPEC-008 §2.1 · 검수 W-6·W-7③ · FE 수정 판 2 · 운영 결함 2026-10-06).
 * 안전본은 `srcdoc` 이 아니라 부모가 iframe 첫 문서에 써 넣는다 — `about:srcdoc` 이동을 데스크톱 셸이 취소했다.
 */

let open: ReturnType<typeof vi.fn>;
beforeEach(() => {
  open = vi.fn();
  vi.stubGlobal("open", open);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const CSP = `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'">`;

function mount(body: string) {
  render(<MailFrame html={`${CSP}${body}`} messageId="m1" />);
  const frame = screen.getByTitle("메일 본문") as HTMLIFrameElement;
  return { frame, doc: frame.contentDocument! };
}

it("srcdoc 이동 없이 안전본을 iframe 첫 문서에 써 넣는다 — 샌드박스는 그대로(allow-scripts 없음)", () => {
  const { frame, doc } = mount("<p>일정표 본문</p>");
  expect(frame.hasAttribute("srcdoc")).toBe(false);
  expect(frame.getAttribute("sandbox")).toBe(MAIL_SANDBOX);
  expect(MAIL_SANDBOX).not.toContain("allow-scripts");
  expect(doc.body.textContent).toContain("일정표 본문");
  expect(doc.getElementById("ax-frame-style")).not.toBeNull();
});

it("클릭·가운데 클릭 모두 부모가 열고(새 탭 noopener,noreferrer) 한 번씩만 연다 — load 가 다시 와도", async () => {
  const { frame, doc } = mount('<a id="ok" href="https://noeul.example/a?t=1">일정</a>');
  fireEvent.load(frame);
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
  const { doc } = mount('<a id="bad" href="javascript:alert(1)">보기</a>');
  const click = new MouseEvent("click", { bubbles: true, cancelable: true });
  doc.getElementById("bad")!.dispatchEvent(click);
  expect(click.defaultPrevented).toBe(true);
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(open).not.toHaveBeenCalled();
});

it("원격 이미지는 처음부터 우리 프록시로만 달리고(배너 없음), 실패하면 깨진 아이콘 대신 alt 글자만 남는다(FE 수정 판 2)", () => {
  const { doc } = mount('<img id="a" alt="로고" data-ax-remote-src="https://tracker.example/p.png?u=1"><img id="b" alt="배너" data-ax-remote-src="https://cdn.example/b.jpg">');
  expect(doc.getElementById("a")!.getAttribute("src")).toBe(`/api/inbox/mail/m1/remote-image?u=${encodeURIComponent("https://tracker.example/p.png?u=1")}`);
  expect(doc.getElementById("b")!.getAttribute("src")).toMatch(/^\/api\/inbox\/mail\/m1\/remote-image\?u=/);
  expect(screen.queryByRole("button", { name: "이미지 보기" })).toBeNull();
  doc.getElementById("a")!.dispatchEvent(new Event("error"));
  expect(doc.getElementById("a")).toBeNull();
  expect(doc.querySelector(".ax-img-missing")?.textContent).toBe("로고");
});
