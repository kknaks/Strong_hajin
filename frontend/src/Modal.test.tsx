import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Toast } from "./Modal";

afterEach(cleanup);

function toastNode() {
  return screen.getByText("완료 처리했습니다").closest(".toast") as HTMLElement;
}

describe("Toast", () => {
  it("tone 이 없으면 지금 그대로다 — 클래스·role·글리프 하나 그대로", () => {
    const { container } = render(<Toast message="완료 처리했습니다" onClose={() => {}} />);
    const toast = container.firstElementChild as HTMLElement;
    expect(toast.className).toBe("toast");
    expect(toast.getAttribute("role")).toBe("status");
    // 메시지는 감싸는 상자 없이 텍스트 노드 그대로 — tone 이 붙을 때만 상자가 생긴다.
    expect(toast.firstChild?.nodeType).toBe(Node.TEXT_NODE);
    expect(toast.firstChild?.textContent).toBe("완료 처리했습니다");
    // 닫기 글리프 하나뿐 — tone 아이콘이 끼어들지 않는다.
    expect(toast.querySelectorAll("svg").length).toBe(1);
  });

  it("success 는 check 글리프를 accent 로 앞에 세우고 role 은 status 를 지킨다", () => {
    const { container } = render(<Toast message="완료 처리했습니다" onClose={() => {}} tone="success" />);
    const toast = container.firstElementChild as HTMLElement;
    expect(toast.className).toBe("toast success");
    expect(toast.getAttribute("role")).toBe("status");
    const icon = toast.querySelector(".toast-icon") as SVGElement;
    expect(icon).toBeTruthy();
    expect(icon.getAttribute("width")).toBe("16");
    // v2 07: 16px 은 stroke 1.5 · fill none
    expect(icon.getAttribute("stroke-width")).toBe("1.5");
    expect(icon.getAttribute("fill")).toBe("none");
    // check 글리프 (v2 09 체크박스가 쓰는 path 그대로)
    expect(icon.querySelector("path")?.getAttribute("d")).toBe("m3.5 8.5 3 3 6-6");
  });

  it("error 는 alert 글리프를 세우고 role 을 alert 로 올린다", () => {
    const { container } = render(<Toast message="처리하지 못했습니다" onClose={() => {}} tone="error" />);
    const toast = container.firstElementChild as HTMLElement;
    expect(toast.className).toBe("toast error");
    expect(toast.getAttribute("role")).toBe("alert");
    const icon = toast.querySelector(".toast-icon") as SVGElement;
    expect(icon.querySelector("circle")).toBeTruthy();
    expect(icon.getAttribute("aria-hidden")).toBe("true");
  });

  it("tone 이 있어도 실행취소와 닫기는 그대로 동작한다", () => {
    const onAction = vi.fn();
    const onClose = vi.fn();
    render(<Toast action={{ label: "실행취소", onAction }} message="완료 처리했습니다" onClose={onClose} tone="success" />);
    fireEvent.click(screen.getByRole("button", { name: "실행취소" }));
    expect(onAction).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "알림 지우기" }));
    expect(onClose).toHaveBeenCalledTimes(2);
    expect(toastNode()).toBeTruthy();
  });

  it("4초 뒤에 스스로 닫는다 (v2 14)", () => {
    vi.useFakeTimers();
    const onClose = vi.fn();
    render(<Toast message="완료 처리했습니다" onClose={onClose} tone="success" />);
    expect(onClose).not.toHaveBeenCalled();
    vi.advanceTimersByTime(4000);
    expect(onClose).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });
});
