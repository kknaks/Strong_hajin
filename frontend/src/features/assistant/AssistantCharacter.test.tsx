import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import provenance from "../../../assets/assistant-character/provenance.json";
import { AssistantCharacter, AssistantLauncher } from "./AssistantCharacter";
import { assistantCharacterCatalog } from "./assistantCharacterAssets";
import type { AssistantPresentationState } from "./assistantPresentation";


const idle: AssistantPresentationState = {
  kind: "idle",
  label: "대기 중",
  prompt: "오늘 할 일을 정리해줘",
};

describe("AssistantCharacter", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("exposes the same named identity and status in compact profiles", () => {
    render(<AssistantCharacter size="profile" state={{ ...idle, kind: "working", label: "처리 중" }} />);
    expect(screen.getByRole("img", { name: "AX assistant · 크림 고양이 · 처리 중" })).toBeTruthy();
  });

  it("connects every approved catalog entry to a production image", () => {
    expect(assistantCharacterCatalog).toHaveLength(9);
    expect(new Set(assistantCharacterCatalog.map((asset) => asset.key)).size).toBe(9);
    expect(assistantCharacterCatalog.map((asset) => asset.key)).toEqual(provenance.assets.map((asset) => asset.key));
    for (const asset of assistantCharacterCatalog) {
      expect(asset.productionApproved).toBe(true);
      expect(asset.src).toMatch(/\.avif$/);
      const { container } = render(<AssistantCharacter characterKey={asset.key} size="profile" state={idle} />);
      expect(container.querySelector("img")?.getAttribute("src")).toBe(asset.src);
      cleanup();
    }
  });

  it("keeps a static poster available until the production image loads and after an image error", () => {
    const { container, rerender } = render(<AssistantCharacter state={idle} />);
    const image = container.querySelector("img")!;
    expect(container.querySelector(".scax-character__fallback")).toBeTruthy();

    fireEvent.load(image);
    expect(container.querySelector(".scax-character__fallback")).toBeNull();
    rerender(<AssistantCharacter characterKey="red-panda" state={idle} />);
    expect(container.querySelector(".scax-character__fallback")?.textContent).toBe("레");
    fireEvent.error(image);
    expect(container.querySelector(".scax-character__fallback")).toBeTruthy();
  });

  it("opens from the character and only prefills from the separate DOM prompt", () => {
    const onOpen = vi.fn();
    const onPrefill = vi.fn();
    render(<AssistantLauncher onOpen={onOpen} onPrefill={onPrefill} state={idle} />);

    fireEvent.click(screen.getByRole("button", { name: "AX" }));
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(onPrefill).not.toHaveBeenCalled();
    expect(screen.getByRole("status").textContent).toBe("AX 상태: 대기 중");

    const launcher = screen.getByRole("button", { name: "AX" }).closest(".scax-agent") as HTMLElement;
    fireEvent.mouseEnter(launcher);
    expect(launcher.getAttribute("data-assistant-state")).toBe("hover");
    expect(screen.getByRole("status").textContent).toBe("AX 상태: AX와 대화");
    fireEvent.mouseLeave(launcher);
    expect(launcher.getAttribute("data-assistant-state")).toBe("idle");

    fireEvent.click(screen.getByRole("button", { name: "오늘 할 일을 정리해줘" }));
    expect(onPrefill).toHaveBeenCalledWith("오늘 할 일을 정리해줘");
    expect(onOpen).toHaveBeenCalledTimes(2);
  });

  it("pauses decorative motion while the document is hidden", () => {
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    render(<AssistantLauncher onOpen={vi.fn()} onPrefill={vi.fn()} state={idle} />);
    expect(screen.getByRole("button", { name: "AX" }).closest(".scax-agent")?.classList.contains("scax-agent--motion-paused")).toBe(true);
  });
});
