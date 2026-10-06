import { afterEach, describe, expect, it, vi } from "vitest";

import {
  addIntegrationRooms,
  disconnectIntegration,
  markInboxAllRead,
  markInboxRoomRead,
  removeIntegrationRoom,
  replyToInboxRoom,
  resetKakaoAccount,
} from "./api";

/* 본문 없는 성공 응답 — 202(방 추가)·204·빈 200 — 에서 JSON 을 풀지 않는다(FE 수정 판 4 · 「Unexpected end of JSON input」). */

afterEach(() => vi.unstubAllGlobals());

const reply = (response: () => Response) => vi.stubGlobal("fetch", vi.fn(async () => response()));

describe("빈 본문 성공", () => {
  it("202 + 빈 본문(방 추가)은 성공이다", async () => {
    reply(() => new Response(null, { status: 202 }));
    await expect(addIntegrationRooms("i", ["C01"])).resolves.toBeUndefined();
  });

  it("202 + Content-Length 0 · 204 · 빈 200 도 성공이다", async () => {
    reply(() => new Response("", { status: 202, headers: { "Content-Length": "0", "Content-Type": "application/json" } }));
    await expect(markInboxRoomRead("r", "1.0")).resolves.toBeUndefined();
    reply(() => new Response(null, { status: 204 }));
    await expect(removeIntegrationRoom("i", "r")).resolves.toBeUndefined();
    await expect(disconnectIntegration("i")).resolves.toBeUndefined();
    await expect(resetKakaoAccount()).resolves.toBeUndefined();
    reply(() => new Response("", { status: 200 }));
    await expect(markInboxAllRead()).resolves.toBeUndefined();
  });

  it("본문이 있는 202 는 그대로 푼다(답장 접수 local_id)", async () => {
    reply(() => new Response(JSON.stringify({ local_id: "L1" }), { status: 202, headers: { "Content-Type": "application/json" } }));
    await expect(replyToInboxRoom("r", { text: "hi", files: [] }, "k")).resolves.toEqual({ local_id: "L1" });
  });
});
