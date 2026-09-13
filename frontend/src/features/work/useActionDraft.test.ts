import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ACTION_DRAFT_STORAGE_KEY, useActionDraft } from "./useActionDraft";

type Draft = { title: string };

function config(actionId: string, principalId = "jiho", pending = true) {
  return {
    principalId,
    actionId,
    baseSubmissionVersion: 1,
    baseDraft: { title: `${actionId} 원안` },
    pending,
    sanitize: (value: Record<string, unknown>): Draft => ({ title: String(value.title ?? "") }),
  };
}

describe("useActionDraft", () => {
  afterEach(() => {
    cleanup();
    window.localStorage.clear();
  });

  it("keeps separate browser drafts for two ActionItems and restores each one", () => {
    const first = renderHook(() => useActionDraft(config("action-1")));
    const second = renderHook(() => useActionDraft(config("action-2")));
    act(() => first.result.current.setDraft({ title: "첫 대화 초안" }));
    act(() => second.result.current.setDraft({ title: "둘째 대화 초안" }));
    first.unmount();
    second.unmount();

    const restoredFirst = renderHook(() => useActionDraft(config("action-1")));
    const restoredSecond = renderHook(() => useActionDraft(config("action-2")));

    expect(restoredFirst.result.current.draft.title).toBe("첫 대화 초안");
    expect(restoredSecond.result.current.draft.title).toBe("둘째 대화 초안");
    expect(restoredFirst.result.current.restored).toBe(true);
    expect(restoredSecond.result.current.restored).toBe(true);
  });

  it("removes only the resolved ActionItem draft and leaves another principal isolated", async () => {
    const mine = renderHook(({ pending }) => useActionDraft(config("action-1", "jiho", pending)), {
      initialProps: { pending: true },
    });
    const theirs = renderHook(() => useActionDraft(config("action-1", "mina")));
    act(() => mine.result.current.setDraft({ title: "내 초안" }));
    act(() => theirs.result.current.setDraft({ title: "다른 사람 초안" }));

    mine.rerender({ pending: false });

    await waitFor(() => {
      const records = Object.values(JSON.parse(window.localStorage.getItem(ACTION_DRAFT_STORAGE_KEY) ?? "{}")) as Array<{ principal_id: string }>;
      expect(records.map((record) => record.principal_id)).toEqual(["mina"]);
    });
  });
});
