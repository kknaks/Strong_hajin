import type { ConnectStart } from "../../lib/api";
import { openExternal } from "../../lib/shell";

/** 동의 화면이 사는 곳 — 서버를 믿지만 이동 대상은 고정돼 있으므로 한 번 더 막는다(심층 방어 · 검수 W-4). */
const CONSENT_HOSTS = ["accounts.google.com", "slack.com"];

export function isConsentUrl(url: string): boolean {
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "https:") return false;
    return CONSENT_HOSTS.some((host) => parsed.hostname === host || parsed.hostname.endsWith(`.${host}`));
  } catch {
    return false;
  }
}

/**
 * 연결 시작 — 서버가 준 **동의 URL** 로 간다(SPEC-008 §4.2 F-2).
 *
 * - 데스크톱 셸: `open_external` 로 OS 기본 브라우저에서 연다. 동의는 거기서 끝나고 앱은 모르므로, 설정 화면이
 *   창의 `focus`/`visibilitychange` 때 연동 목록을 다시 읽어 갱신한다(N-3).
 * - 브라우저: 이 창을 그 URL 로 옮긴다. 콜백이 `?surface=settings&tab=…&connect=ok|denied` 로 돌려보낸다(N-2).
 *
 * 돌려주는 값: `"browser"`(이 창이 떠난다) · `"external"`(OS 브라우저에서 열림) · `"failed"`(셸이 열지 못했다 · 동의 URL 이 허용 목록 밖이다).
 */
export async function beginConsent(start: ConnectStart, navigate: (url: string) => void = (url) => window.location.assign(url)): Promise<"browser" | "external" | "failed"> {
  if (!isConsentUrl(start.authorize_url)) return "failed";
  const outcome = await openExternal(start.authorize_url);
  if (outcome === "opened") return "external";
  if (outcome === "failed") return "failed";
  navigate(start.authorize_url);
  return "browser";
}
