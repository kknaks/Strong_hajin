import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AxDraftCard, type AxDraftSource } from "../action/AxDraftCard";
import { Button } from "../../ds/Button";

/**
 * 채팅 서랍 안 사람 행동 단추 — **어느 상태에서도 파랑이 되지 않는다** (SPEC-002 §2.9 「채팅 서랍 안의 색」 ·
 * WORK-009 2a-2 · E2E-3).
 *
 * 원인은 명시도였다. DS 의 `.scax-button--solid-primary:hover`(0,2,0)가 `:not(:disabled)` 없이 같은 명시도의
 * `.scax-button:disabled`(0,2,0)보다 뒤에 있어, 「등록 중…」(비활성)인데 포인터가 위에 있으면 accent-strong 이 이겼다.
 * jsdom 은 cascade 를 계산하지 않으므로 **규칙이 서 있는지와 그 명시도**를 잰다(`ds/HoverContrast.test.tsx` 방식).
 */

async function flatCss(path: string): Promise<string> {
  // @ts-expect-error — 이 리포는 @types/node 를 두지 않는다.
  const { readFileSync } = await import("node:fs");
  return (readFileSync(path, "utf8") as string).replace(/\s+/g, "");
}

/** 클래스·의사 클래스 수(명시도의 가운데 자리). `:not(x)` 는 괄호 안만 센다 — CSS 규칙과 같다. */
function classWeight(selector: string): number {
  return (selector.replace(/:not\(/g, "(").match(/\.[\w-]+|:[\w-]+/g) ?? []).length;
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("서랍 안 비활성 단추 — hover 와 겹쳐도 비활성 모양", () => {
  const solid = [".scax-drawer--chat .scax-button--solid-primary:disabled", ".scax-drawer--chat .scax-button--solid-danger:disabled"];
  const quiet = [".scax-drawer--chat .scax-button--outlined-neutral:disabled", ".scax-drawer--chat .scax-button--text-neutral:disabled"];

  it("주 단추(solid)의 비활성은 `.action-task-card` 의 비활성과 같은 값이다", async () => {
    const css = await flatCss("src/styles/ax.css");
    const cardDisabled = "{border-color:var(--scax-color-line);background:var(--scax-color-surface-alt);color:var(--scax-color-ink-disabled)}";
    expect(css).toContain(`.action-task-card>.action-task-actions.scax-button:disabled${cardDisabled.replace("}", ";}")}`);
    expect(css).toContain(`${solid.map((s) => s.replace(/\s+/g, "")).join(",")}${cardDisabled}`);
  });

  it("테두리·글자 단추의 비활성은 DS 비활성 값 그대로다", async () => {
    const css = await flatCss("src/styles/ax.css");
    expect(css).toContain(
      `${quiet.map((s) => s.replace(/\s+/g, "")).join(",")}{border-color:transparent;background:var(--scax-color-fill-weak);color:var(--scax-color-ink-disabled)}`,
    );
  });

  it("서랍 안 비활성 규칙이 DS hover 규칙보다 명시도가 높다 — 순서와 무관하게 이긴다", async () => {
    const ds = await flatCss("src/styles/components.css");
    // DS 원본은 고치지 않는다(DS-gaps) — hover 규칙은 여전히 `:not(:disabled)` 없이 0,2,0 이다.
    for (const hover of [".scax-button--solid-primary:hover", ".scax-button--solid-danger:hover", ".scax-button--outlined-neutral:hover", ".scax-button--text-neutral:hover"]) {
      expect(ds).toContain(`${hover}{`);
      for (const rule of [...solid, ...quiet]) expect(classWeight(rule)).toBeGreaterThan(classWeight(hover));
    }
  });

  it("서랍 안 비활성 규칙에는 파랑 토큰이 없다", async () => {
    const css = await flatCss("src/styles/ax.css");
    const start = css.indexOf(".scax-drawer--chat.scax-button--solid-primary:disabled");
    const block = css.slice(start, css.indexOf("}", css.indexOf(".scax-drawer--chat.scax-button--text-neutral:disabled")) + 1);
    expect(start).toBeGreaterThan(-1);
    expect(block).not.toMatch(/accent/);
  });
});

describe("「근거 N개 더 보기」 — 사람 행동이라 검정 계열 글자 단추", () => {
  it("글자와 hover 가 잉크 계열이고 파랑 토큰을 쓰지 않는다", async () => {
    const css = await flatCss("src/styles/ax.css");
    const rule = css.slice(css.indexOf(".scax-sources__more{"), css.indexOf("}", css.indexOf(".scax-sources__more{")) + 1);
    expect(rule).toContain("color:var(--scax-color-ink-neutral)");
    expect(rule).not.toMatch(/accent/);
    expect(css).toContain(".scax-sources__more:hover{color:var(--scax-color-ink)}");
  });
});

describe("「상세 보기」 펼치기 머리 — 서랍 안에서는 검정 계열 (2a-2 fix1 · W1)", () => {
  it("서랍 스코프로만 잉크 계열을 주고, 서랍 밖 원래 규칙은 그대로 둔다", async () => {
    const css = await flatCss("src/styles/ax.css");
    expect(css).toContain(".scax-drawer--chat.scax-preview>summary{color:var(--scax-color-ink-neutral)}");
    expect(css).toContain(".scax-drawer--chat.scax-preview>summary:hover{color:var(--scax-color-ink)}");
    // 서랍 밖(판단 상세 등)은 앱 DS 그대로다 — 원래 규칙을 지우지 않았다.
    expect(css).toContain(".scax-preview>summary{cursor:pointer;color:var(--scax-color-accent);");
    expect(classWeight(".scax-drawer--chat .scax-preview>summary")).toBeGreaterThan(classWeight(".scax-preview>summary"));
  });
});

describe("진행 중 단추가 위 규칙이 겨누는 클래스로 선다", () => {
  const source: AxDraftSource = {
    actionId: "action-1",
    kind: "task",
    title: "KPI 설정",
    round: 1,
    state: "pending",
    contract: { editor: "task", base_submission_version: 1, values: { title: "KPI 설정" }, fields: [] },
    materials: [],
    commands: [
      { id: "reject", label: "거절", tone: "neutral" },
      { id: "confirm", label: "등록", tone: "primary" },
    ],
    createdAt: null,
  };

  it("AX 초안 카드 「등록 중…」은 비활성 solid-primary — 거절도 같은 순간 비활성 outlined-neutral 이다", () => {
    const onCommand = vi.fn(() => new Promise<void>(() => undefined));
    render(
      <aside className="scax-drawer scax-drawer--chat">
        <AxDraftCard onCommand={onCommand} source={source} />
      </aside>,
    );
    fireEvent.click(screen.getByRole("button", { name: "등록" }));
    const busy = screen.getByRole("button", { name: "등록 중…" }) as HTMLButtonElement;
    expect(busy.disabled).toBe(true);
    expect(busy.className).toContain("scax-button--solid-primary");
    expect(busy.closest(".scax-drawer--chat")).toBeTruthy();
    const reject = within(busy.closest("footer")!).getByRole("button", { name: "거절" }) as HTMLButtonElement;
    expect(reject.disabled).toBe(true);
    expect(reject.className).toContain("scax-button--outlined-neutral");
  });

  it("DS 단추는 variant·tone 을 클래스 하나로 낸다 — 서랍 규칙이 겨누는 이름이다", () => {
    render(
      <>
        <Button disabled tone="primary" variant="solid">a</Button>
        <Button disabled tone="danger" variant="solid">b</Button>
        <Button disabled variant="text">c</Button>
        <Button disabled>d</Button>
      </>,
    );
    expect(screen.getByRole("button", { name: "a" }).className).toContain("scax-button--solid-primary");
    expect(screen.getByRole("button", { name: "b" }).className).toContain("scax-button--solid-danger");
    expect(screen.getByRole("button", { name: "c" }).className).toContain("scax-button--text-neutral");
    expect(screen.getByRole("button", { name: "d" }).className).toContain("scax-button--outlined-neutral");
  });
});
