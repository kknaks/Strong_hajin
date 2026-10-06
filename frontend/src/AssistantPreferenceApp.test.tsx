import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";


const jsonResponse = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status,
  headers: { "Content-Type": "application/json" },
});

describe("assistant character preference", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("restores the session choice and synchronizes a saved picker choice to the launcher", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/auth/me") return jsonResponse({
        member_id: "mina",
        display_name: "민아 (구성원)",
        organizations: [],
        capabilities: [],
        assistant_character: { character_key: "red-panda", version: 1 },
      });
      if (path === "/api/profile/preferences/assistant-character" && init?.method === "PUT") {
        return jsonResponse({ character_key: "rabbit", version: 2 });
      }
      return jsonResponse([]);
    });
    vi.stubGlobal("fetch", fetchMock);

    const { container } = render(<App />);
    await screen.findByRole("navigation", { name: "제품 탐색" });
    expect(container.querySelector(".scax-agent--ax [data-character-key]")?.getAttribute("data-character-key"))
      .toBe("red-panda");

    // 캐릭터 고르기는 설정 › 프로필 설정으로 옮겨 왔다(WORK-011 FE-b · D-48)
    fireEvent.click(screen.getByRole("button", { name: "설정" }));
    fireEvent.click(await screen.findByRole("button", { name: "프로필 설정" }));
    const picker = await screen.findByRole("region", { name: "AX 캐릭터" });
    fireEvent.click(within(picker).getByRole("radio", { name: /토끼/ }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      "/api/profile/preferences/assistant-character",
      expect.objectContaining({
        method: "PUT",
        body: JSON.stringify({ character_key: "rabbit", expected_version: 1 }),
      }),
    ));
    expect(container.querySelector(".scax-agent--ax [data-character-key]")?.getAttribute("data-character-key"))
      .toBe("rabbit");
  });

  it("falls back without rewriting an unknown saved key and rolls back an optimistic choice on failure", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path === "/api/auth/me") return jsonResponse({
        member_id: "mina",
        display_name: "민아 (구성원)",
        organizations: [],
        capabilities: [],
        assistant_character: { character_key: "retired-fox", version: 3 },
      });
      if (path === "/api/profile/preferences/assistant-character" && init?.method === "PUT") {
        return jsonResponse({ detail: "AX 캐릭터 설정이 다른 곳에서 변경됐습니다." }, 409);
      }
      return jsonResponse([]);
    });
    vi.stubGlobal("fetch", fetchMock);

    const { container } = render(<App />);
    await screen.findByRole("navigation", { name: "제품 탐색" });
    expect(container.querySelector(".scax-agent--ax [data-character-key]")?.getAttribute("data-character-key"))
      .toBe("cream-cat");

    // 예전 진입점(신원 줄 · 설정 줄의 모달)은 없다 — 프로필 설정이 유일한 자리다(W-14)
    expect(screen.queryByRole("button", { name: "내 AX 캐릭터" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "설정" }));
    fireEvent.click(await screen.findByRole("button", { name: "프로필 설정" }));
    const picker = await screen.findByRole("region", { name: "AX 캐릭터" });
    expect(within(picker).getByRole("status").textContent).toContain("저장값을 바꾸지 않습니다");
    fireEvent.click(within(picker).getByRole("radio", { name: /곰/ }));

    expect((await within(picker).findByRole("alert")).textContent).toContain("다른 곳에서 변경됐습니다");
    expect(container.querySelector(".scax-agent--ax [data-character-key]")?.getAttribute("data-character-key"))
      .toBe("cream-cat");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/profile/preferences/assistant-character",
      expect.objectContaining({
        body: JSON.stringify({ character_key: "bear", expected_version: 3 }),
        method: "PUT",
      }),
    );
  });
});
