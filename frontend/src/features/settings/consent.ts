import type { ConnectStart } from "../../lib/api";
import { openExternal } from "../../lib/shell";

/**
 * 연결 시작 — 서버가 준 **동의 URL** 로 간다(SPEC-008 §4.2 F-2).
 *
 * - 데스크톱 셸: `open_external` 로 OS 기본 브라우저에서 연다. 동의는 거기서 끝나고 앱은 모르므로, 설정 화면이
 *   창의 `focus`/`visibilitychange` 때 연동 목록을 다시 읽어 갱신한다(N-3).
 * - 브라우저: 이 창을 그 URL 로 옮긴다. 콜백이 `?surface=settings&tab=…&connect=ok|denied` 로 돌려보낸다(N-2).
 *
 * 돌려주는 값: `"browser"`(이 창이 떠난다) · `"external"`(OS 브라우저에서 열림) · `"failed"`(셸이 열지 못했다).
 */
export async function beginConsent(start: ConnectStart, navigate: (url: string) => void = (url) => window.location.assign(url)): Promise<"browser" | "external" | "failed"> {
  const outcome = await openExternal(start.authorize_url);
  if (outcome === "opened") return "external";
  if (outcome === "failed") return "failed";
  navigate(start.authorize_url);
  return "browser";
}
