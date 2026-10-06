import { describe, expect, it, vi } from "vitest";

import { beginConsent, isConsentUrl } from "./consent";

describe("동의 URL 허용 목록 (검수 W-4)", () => {
  it("https 의 accounts.google.com · slack.com(하위 포함)만", () => {
    expect(isConsentUrl("https://accounts.google.com/o/oauth2/v2/auth?x=1")).toBe(true);
    expect(isConsentUrl("https://noeul.slack.com/oauth/v2/authorize")).toBe(true);
    expect(isConsentUrl("https://slack.com/oauth/v2/authorize")).toBe(true);
    expect(isConsentUrl("http://accounts.google.com/")).toBe(false);
    expect(isConsentUrl("https://evil-slack.com/")).toBe(false);
    expect(isConsentUrl("https://accounts.google.com.evil.example/")).toBe(false);
    expect(isConsentUrl("javascript:alert(1)")).toBe(false);
  });

  it("허용 목록 밖이면 이동하지 않고 실패", async () => {
    const navigate = vi.fn();
    expect(await beginConsent({ authorize_url: "https://evil.example/", state: "s" }, navigate)).toBe("failed");
    expect(navigate).not.toHaveBeenCalled();
    expect(await beginConsent({ authorize_url: "https://accounts.google.com/x", state: "s" }, navigate)).toBe("browser");
    expect(navigate).toHaveBeenCalledWith("https://accounts.google.com/x");
  });
});
