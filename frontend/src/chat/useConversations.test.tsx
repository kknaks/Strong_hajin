import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import type { Conversation } from "../viewModels";
import { useConversations } from "./useConversations";

const running: Conversation = {
  conversation_id: "conversation-1",
  title: "마감 업무 확인",
  version: 3,
  messages: [],
  turns: [{
    turn_id: "turn-1", state: "running", progress_state: "preparing",
    provider_run_ref: null, provider_session_ref: null, error: null,
  }],
  context_references: [],
  tool_invocations: [],
  actions: [],
};

const completed: Conversation = {
  ...running,
  // Provider progress can change without changing the conversation's command version.
  turns: [{ ...running.turns[0], state: "completed", progress_state: "completed" }],
  messages: [{
    message_id: "answer-1", turn_id: "turn-1", role: "assistant",
    body: "오늘 마감 업무는 두 건입니다.", sequence: 1, state: "accepted", body_state: "final",
  }],
};

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it("shows the completed answer when a detail response takes longer than the polling interval", async () => {
  vi.useFakeTimers();
  vi.stubGlobal("fetch", vi.fn(async () => {
    await new Promise((resolve) => setTimeout(resolve, 1200));
    return new Response(JSON.stringify(completed), { headers: { "Content-Type": "application/json" } });
  }));
  const onError = vi.fn();
  const { result } = renderHook(() => useConversations({ personaId: "mina", isOpen: false, onError }));
  act(() => result.current.adopt(running));

  await act(async () => { await vi.advanceTimersByTimeAsync(800); });
  await act(async () => { await vi.advanceTimersByTimeAsync(1200); });

  expect(result.current.activeConversation?.messages[0]?.body).toBe("오늘 마감 업무는 두 건입니다.");
  expect(result.current.isProcessing).toBe(false);
  expect(onError).not.toHaveBeenCalled();
});

it("continues polling after a slow failed read and then shows the answer", async () => {
  vi.useFakeTimers();
  let reads = 0;
  vi.stubGlobal("fetch", vi.fn(async () => {
    const current = ++reads;
    await new Promise((resolve) => setTimeout(resolve, 1200));
    return current === 1
      ? new Response("unavailable", { status: 503 })
      : new Response(JSON.stringify(completed), { headers: { "Content-Type": "application/json" } });
  }));
  const onError = vi.fn();
  const { result } = renderHook(() => useConversations({ personaId: "mina", isOpen: false, onError }));
  act(() => result.current.adopt(running));

  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(onError).toHaveBeenCalledOnce();
  expect(result.current.isProcessing).toBe(true);
  await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
  expect(result.current.activeConversation?.messages[0]?.body).toBe(completed.messages[0].body);
  expect(result.current.isProcessing).toBe(false);
});

it("does not restart polling or report a late failure after the chat owner unmounts", async () => {
  vi.useFakeTimers();
  const fetchMock = vi.fn(async () => {
    await new Promise((resolve) => setTimeout(resolve, 1200));
    return new Response("unavailable", { status: 503 });
  });
  vi.stubGlobal("fetch", fetchMock);
  const onError = vi.fn();
  const { result, unmount } = renderHook(() => useConversations({ personaId: "mina", isOpen: false, onError }));
  act(() => result.current.adopt(running));
  await act(async () => { await vi.advanceTimersByTimeAsync(800); });
  unmount();
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(fetchMock).toHaveBeenCalledOnce();
  expect(onError).not.toHaveBeenCalled();
});
