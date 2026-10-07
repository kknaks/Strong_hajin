import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { InboxAttachment } from "../../lib/viewModels";
import { resetShellDownloadsForTest } from "../../lib/shellDownloads";
import { AttachmentList, Thumb } from "./InboxAttachments";
import { createInboxEventHub } from "./inboxStream";
import { MailView } from "./MailView";

/*
 * WORK-012 WP1-FE — SH-IMP-014 받기 링크(SPEC-008 §2.2 · OQ-812).
 *
 * - 받기(파일 카드의 받기 단추 · 「모두 다운로드」 · 메일 이미지 받기) = `?download=1` 주소의 **같은 탭 링크** —
 *   `download` 속성도 `target="_blank"` 도 없다(있으면 데스크톱 앱의 웹뷰가 셸 훅 전에 스스로 내려받기로 넘겨 아무 일도 없었다)
 * - 미리보기(썸네일 `<img>`)는 `download` 없는 주소 그대로
 * - 이미지 원본 보기(`Thumb`)는 셸 전역(`hasShell()`)이 있으면 받기 주소·같은 탭, 없으면(웹) 원본 주소·새 탭
 */

type ShellWindow = Window & { __TAURI_INTERNALS__?: unknown };

const ROOM = "r1";
const original = (aid: string) => `/api/inbox/rooms/${ROOM}/attachments/${aid}`;
const download = (aid: string) => `${original(aid)}?download=1`;
const thumb = (aid: string) => `${original(aid)}?variant=thumb`;

const file = (aid: string, name: string, over: Partial<InboxAttachment> = {}): InboxAttachment => ({
  aid,
  name,
  size: 1200,
  mime: "application/octet-stream",
  kind: "file",
  state: "reference",
  ...over,
});

function renderList(attachments: InboxAttachment[]) {
  return render(<AttachmentList attachments={attachments} downloadOf={download} hrefOf={original} thumbOf={thumb} />);
}

/** 받기 링크의 계약 — `?download=1` · `download` 속성 없음 · `target` 없음. */
function expectDownloadLink(link: HTMLElement, href: string) {
  expect(link.getAttribute("href")).toBe(href);
  expect(link.hasAttribute("download")).toBe(false);
  expect(link.hasAttribute("target")).toBe(false);
}

function setShell(present: boolean) {
  if (present) (window as ShellWindow).__TAURI_INTERNALS__ = {};
  else delete (window as ShellWindow).__TAURI_INTERNALS__;
}

beforeEach(() => setShell(false));

afterEach(() => {
  cleanup();
  setShell(false);
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("받기 링크 — `?download=1` 같은 탭 링크 (SH-IMP-014)", () => {
  it("파일 카드 하나의 받기 단추", () => {
    renderList([file("d1", "보고서.zip")]);
    expectDownloadLink(screen.getByRole("link", { name: "보고서.zip 받기" }), download("d1"));
  });

  it("PDF 카드의 받기 단추 — PDF 도 미리보기가 아니라 받기 주소다", () => {
    renderList([file("p1", "범위.pdf", { mime: "application/pdf" })]);
    expectDownloadLink(screen.getByRole("link", { name: "범위.pdf 받기" }), download("p1"));
  });

  it("파일 묶음의 받기 단추 전부", () => {
    renderList([file("d1", "가.zip"), file("d2", "나.docx")]);
    expectDownloadLink(screen.getByRole("link", { name: "가.zip 받기" }), download("d1"));
    expectDownloadLink(screen.getByRole("link", { name: "나.docx 받기" }), download("d2"));
  });

  it("「모두 다운로드」는 받기 주소를 `download` 속성 없이 차례로 누른다", () => {
    vi.useFakeTimers();
    const clicked: Array<{ href: string; download: boolean; target: string }> = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      clicked.push({ href: this.getAttribute("href") ?? "", download: this.hasAttribute("download"), target: this.target });
    });
    renderList([file("d1", "가.zip"), file("d2", "나.docx")]);
    screen.getByRole("button", { name: "모두 다운로드" }).click();
    vi.advanceTimersByTime(1000);
    expect(clicked).toEqual([
      { href: download("d1"), download: false, target: "" },
      { href: download("d2"), download: false, target: "" },
    ]);
  });

  it("받을 수 없는 첨부(만료 · 아직 안 받음)에는 받기 링크가 없다 — 지금 규칙 그대로", () => {
    renderList([file("x1", "옛.zip", { state: "expired" }), file("x2", "대기.zip", { state: "pending" })]);
    expect(screen.queryByRole("link", { name: /받기/ })).toBeNull();
  });
});

describe("이미지 — 미리보기는 그대로 · 원본 보기는 앱/웹이 다르다 (OQ-812)", () => {
  const image = file("i1", "현장.png", { mime: "image/png", kind: "image", size: 276000 });

  it("웹(셸 없음) — 썸네일은 미리보기 주소, 누르면 원본을 새 탭으로(내려받기가 아니다)", () => {
    renderList([image]);
    const img = screen.getByAltText("현장.png");
    expect(img.getAttribute("src")).toBe(thumb("i1"));
    const link = img.closest("a") as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe(original("i1"));
    expect(link.getAttribute("href")).not.toContain("download=1");
    expect(link.getAttribute("target")).toBe("_blank");
  });

  it("앱(셸 있음) — 썸네일은 그대로, 누르면 받기 주소를 같은 탭으로(셸이 받아 저장 + 토스트)", () => {
    setShell(true);
    renderList([image]);
    const img = screen.getByAltText("현장.png");
    expect(img.getAttribute("src")).toBe(thumb("i1"));
    expectDownloadLink(img.closest("a") as HTMLAnchorElement, download("i1"));
  });

  it("앨범 칸도 같다 — 앱에서는 받기 주소, 웹에서는 원본 새 탭", () => {
    const album = [1, 2].map((n) => file(`a${n}`, `사진${n}.jpg`, { mime: "image/jpeg", kind: "album" }));
    renderList(album);
    expect((screen.getByAltText("사진1.jpg").closest("a") as HTMLAnchorElement).getAttribute("target")).toBe("_blank");
    cleanup();
    setShell(true);
    renderList(album);
    expectDownloadLink(screen.getByAltText("사진1.jpg").closest("a") as HTMLAnchorElement, download("a1"));
  });

  it("받기 주소를 주지 않은 `Thumb` 는 앱에서도 지금처럼 원본을 새 탭으로 연다", () => {
    setShell(true);
    render(<Thumb alt="그림" src="/x.png" />);
    const link = screen.getByAltText("그림").closest("a") as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/x.png");
    expect(link.getAttribute("target")).toBe("_blank");
  });
});

describe("메일 첨부 — 미리보기 주소와 받기 주소를 가른다", () => {
  const MAIL = {
    message_id: "m1",
    integration_id: "i-mail",
    account: "haram@company.example",
    thread_id: null,
    subject: "일정표",
    sender: "서지안 <jian@noeul.example>",
    to: [],
    cc: [],
    reply_to: [],
    date: null,
    at: "2026-10-06T00:10:00Z",
    unread: false,
    safe_html: "<p>본문</p>",
    attachments: [
      { aid: "a1", name: "범위.pdf", size: 1200, mime: "application/pdf", kind: "file", state: "reference" },
      { aid: "a2", name: "현장.jpg", size: 2400, mime: "image/jpeg", kind: "image", state: "reference" },
    ],
    sent_replies: [],
  };
  const CARD = {
    kind: "mail" as const,
    message_id: "m1",
    integration_id: "i-mail",
    account: "haram@company.example",
    subject: "일정표",
    sender: "서지안 <jian@noeul.example>",
    at: "2026-10-06T00:10:00Z",
    unread: false,
    attach_count: 2,
    snippet: null,
  };
  const mailPath = "/api/inbox/mail/m1/attachments";

  async function renderMail() {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) =>
        String(input) === "/api/inbox/mail/m1"
          ? new Response(JSON.stringify(MAIL), { status: 200, headers: { "Content-Type": "application/json" } })
          : new Response(null, { status: 204 }),
      ),
    );
    render(<MailView card={CARD} hub={createInboxEventHub()} onRead={() => undefined} />);
    return screen.findByAltText("현장.jpg");
  }

  it("웹 — 이미지 썸네일·원본 보기는 미리보기 주소(새 탭), 받기 둘은 `?download=1`", async () => {
    const img = await renderMail();
    expect(img.getAttribute("src")).toBe(`${mailPath}/a2`);
    const view = img.closest("a") as HTMLAnchorElement;
    expect(view.getAttribute("href")).toBe(`${mailPath}/a2`);
    expect(view.getAttribute("target")).toBe("_blank");
    expectDownloadLink(screen.getByRole("link", { name: "현장.jpg 받기" }), `${mailPath}/a2?download=1`);
    expectDownloadLink(screen.getByRole("link", { name: "범위.pdf 받기" }), `${mailPath}/a1?download=1`);
  });

  it("앱 — 이미지 원본 보기도 받기 주소·같은 탭 · 썸네일은 그대로 미리보기 주소", async () => {
    setShell(true);
    const img = await renderMail();
    expect(img.getAttribute("src")).toBe(`${mailPath}/a2`);
    expectDownloadLink(img.closest("a") as HTMLAnchorElement, `${mailPath}/a2?download=1`);
    await waitFor(() => expect(within(document.body).getByRole("link", { name: "범위.pdf 받기" })).toBeTruthy());
  });
});

/*
 * WORK-012 2루프 E-2 — 앱에서 받기를 누르면 그 첨부에 즉시 「받는 중」(도는 원), 셸 알림(`strong-hajin:download`)이 오면 걷는다.
 * 셸 사건에는 주소가 없다 — 이름이 맞는 것(셸 번호 「(1)」 포함), 아니면 가장 오래된 것을 걷는다. 알림이 안 오면 60초 뒤 걷는다.
 */
describe("받는 중 표시 (2루프 E-2)", () => {
  const shellEvent = (detail: { ok: boolean; filename?: string | null }) =>
    act(() => {
      window.dispatchEvent(new CustomEvent("strong-hajin:download", { detail }));
    });
  const spinners = () => document.querySelectorAll('[data-testid="download-spinner"]').length;
  const stopNavigation = (event: Event) => event.preventDefault();

  beforeEach(() => {
    resetShellDownloadsForTest();
    document.addEventListener("click", stopNavigation);
  });
  afterEach(() => {
    document.removeEventListener("click", stopNavigation);
    resetShellDownloadsForTest();
  });

  it("앱 — 받기를 누르면 바로 도는 원 · 이름이 맞는 성공 알림이 오면 걷힌다", () => {
    setShell(true);
    renderList([file("d1", "보고서.zip")]);
    fireEvent.click(screen.getByRole("link", { name: "보고서.zip 받기" }));
    const busy = screen.getByRole("link", { name: "보고서.zip 받는 중" });
    expect(busy.getAttribute("aria-busy")).toBe("true");
    expect(spinners()).toBe(1);
    shellEvent({ ok: true, filename: "보고서.zip" });
    expect(spinners()).toBe(0);
    expect(screen.getByRole("link", { name: "보고서.zip 받기" })).toBeTruthy();
  });

  it("셸이 번호를 붙인 이름(「보고서 (1).zip」)도 같은 받기로 본다", () => {
    setShell(true);
    renderList([file("d1", "보고서.zip")]);
    fireEvent.click(screen.getByRole("link", { name: "보고서.zip 받기" }));
    shellEvent({ ok: true, filename: "보고서 (1).zip" });
    expect(spinners()).toBe(0);
  });

  it("「모두 다운로드」 는 전부 받는 중으로 서고, 알림마다 그 이름의 것이 걷힌다 · 이름 없는 실패 알림은 가장 오래된 것을 걷는다", () => {
    vi.useFakeTimers();
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    setShell(true);
    renderList([file("d1", "가.zip"), file("d2", "나.docx")]);
    fireEvent.click(screen.getByRole("button", { name: "모두 다운로드" }));
    expect(spinners()).toBe(2);
    shellEvent({ ok: true, filename: "나.docx" });
    expect(screen.getByRole("link", { name: "가.zip 받는 중" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "나.docx 받기" })).toBeTruthy();
    shellEvent({ ok: false });
    expect(spinners()).toBe(0);
  });

  it("알림이 영영 안 오면 60초 뒤 걷힌다", () => {
    vi.useFakeTimers();
    setShell(true);
    renderList([file("d1", "보고서.zip")]);
    fireEvent.click(screen.getByRole("link", { name: "보고서.zip 받기" }));
    act(() => vi.advanceTimersByTime(59_000));
    expect(spinners()).toBe(1);
    act(() => vi.advanceTimersByTime(1_000));
    expect(spinners()).toBe(0);
  });

  it("앱의 썸네일 받기도 그림 위에 도는 원", () => {
    setShell(true);
    renderList([file("i1", "현장.png", { mime: "image/png", kind: "image" })]);
    fireEvent.click(screen.getByAltText("현장.png").closest("a") as HTMLAnchorElement);
    expect(spinners()).toBe(1);
    shellEvent({ ok: true, filename: "현장.png" });
    expect(spinners()).toBe(0);
  });

  it("웹(셸 없음)은 지금 그대로 — 도는 원을 세우지 않는다", () => {
    renderList([file("d1", "보고서.zip")]);
    fireEvent.click(screen.getByRole("link", { name: "보고서.zip 받기" }));
    expect(spinners()).toBe(0);
  });
});
