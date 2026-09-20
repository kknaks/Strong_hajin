import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Toast } from "./Modal";

afterEach(cleanup);

function toastNode() {
  return screen.getByText("완료 처리했습니다").closest(".scax-toast") as HTMLElement;
}

describe("Toast", () => {
  it("tone 이 없으면 지금 그대로다 — 클래스·role·글리프 하나 그대로", () => {
    const { container } = render(<Toast closeLabel="알림 지우기" message="완료 처리했습니다" onClose={() => {}} />);
    const toast = container.firstElementChild as HTMLElement;
    expect(toast.className).toBe("scax-toast");
    expect(toast.getAttribute("role")).toBe("status");
    // 메시지는 감싸는 상자 없이 텍스트 노드 그대로 — tone 이 붙을 때만 상자가 생긴다.
    expect(toast.firstChild?.nodeType).toBe(Node.TEXT_NODE);
    expect(toast.firstChild?.textContent).toBe("완료 처리했습니다");
    // 닫기 글리프 하나뿐 — tone 아이콘이 끼어들지 않는다.
    expect(toast.querySelectorAll("svg").length).toBe(1);
  });

  it("success 는 check 글리프를 accent 로 앞에 세우고 role 은 status 를 지킨다", () => {
    const { container } = render(<Toast closeLabel="알림 지우기" message="완료 처리했습니다" onClose={() => {}} tone="success" />);
    const toast = container.firstElementChild as HTMLElement;
    expect(toast.className).toBe("scax-toast success");
    expect(toast.getAttribute("role")).toBe("status");
    const icon = toast.querySelector(".toast-icon") as SVGElement;
    expect(icon).toBeTruthy();
    expect(icon.getAttribute("width")).toBe("16");
    // v2 07: 16px 은 stroke 1.5 · fill none
    expect(icon.getAttribute("stroke-width")).toBe("1.5");
    expect(icon.getAttribute("fill")).toBe("none");
    // check 글리프 — 바퀴 4 에서 새 DS 의 24 그리드 path 로 갈렸다
    expect(icon.querySelector("path")?.getAttribute("d")).toBe("M20 6 9 17l-5-5");
  });

  it("persist 면 스스로 사라지지 않는다 — 함께 온 단추를 누를 시간이 있다 (4차 발주 6)", () => {
    vi.useFakeTimers();
    try {
      const onClose = vi.fn();
      const { rerender } = render(
        <Toast action={{ label: "다시 불러오기", onAction: () => {} }} closeLabel="알림 지우기" message="완료 처리했습니다" onClose={onClose} persist />,
      );
      vi.advanceTimersByTime(10_000);
      expect(onClose).not.toHaveBeenCalled();
      // 켜지 않으면 예전과 똑같이 4초 뒤에 닫힌다.
      rerender(<Toast closeLabel="알림 지우기" message="완료 처리했습니다" onClose={onClose} />);
      vi.advanceTimersByTime(4_000);
      expect(onClose).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("error 는 alert 글리프를 세우고 role 을 alert 로 올린다", () => {
    const { container } = render(<Toast closeLabel="알림 지우기" message="처리하지 못했습니다" onClose={() => {}} tone="error" />);
    const toast = container.firstElementChild as HTMLElement;
    expect(toast.className).toBe("scax-toast error");
    expect(toast.getAttribute("role")).toBe("alert");
    const icon = toast.querySelector(".toast-icon") as SVGElement;
    expect(icon.querySelector("circle")).toBeTruthy();
    expect(icon.getAttribute("aria-hidden")).toBe("true");
  });

  it("tone 이 있어도 실행취소와 닫기는 그대로 동작한다", () => {
    const onAction = vi.fn();
    const onClose = vi.fn();
    render(<Toast closeLabel="알림 지우기" action={{ label: "실행취소", onAction }} message="완료 처리했습니다" onClose={onClose} tone="success" />);
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
    render(<Toast closeLabel="알림 지우기" message="완료 처리했습니다" onClose={onClose} tone="success" />);
    expect(onClose).not.toHaveBeenCalled();
    vi.advanceTimersByTime(4000);
    expect(onClose).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });
});
