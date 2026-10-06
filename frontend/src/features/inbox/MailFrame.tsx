import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "../../ds/Button";
import { inboxRemoteImageUrl } from "../../lib/api";
import { inboxScreen as copy } from "../../lib/labels";
import { openLink } from "./inboxStream";

/**
 * HTML 메일 본문 — **샌드박스 iframe** (SPEC-008 §2.1 F-3 · AC-08b · N-4 · W3-6).
 *
 * - 담는 것은 서버가 소독한 **안전본**(`safe_html` — 맨 앞 CSP meta · script · on* 속성 · form · iframe 제거 · 원격 이미지 `src` 걷음 ·
 *   인용 `<details>`)뿐이다. 원문을 여기서 다시 고치지 않는다.
 * - `sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox"` — **`allow-scripts` 는 절대 넣지 않는다.**
 *   스크립트가 없으니 `allow-same-origin` 은 안전하고, 그 덕에 부모가 문서를 읽는다.
 * - **높이는 부모가** `contentDocument` 높이를 재서 맞춘다(스크립트 없는 iframe 은 postMessage 를 못 보낸다).
 * - **링크는 부모가 가로챈다** — 웹이면 새 탭(`noopener`), 데스크톱이면 `open_external`(iframe 안 `_blank` 는 셸이 막는다).
 * - **원격 이미지는 기본 차단**, 「이미지 보기」를 누르면 서버 프록시(`remote-image`)로만 단다(추적 픽셀 · N-5).
 * - 글자 모양은 앱 토큰 값을 iframe 에 옮겨 단다(iframe 은 부모 CSS 를 물려받지 않는다) — 시안 `.scax-mail-html` 규칙 그대로.
 */

export const MAIL_SANDBOX = "allow-same-origin allow-popups allow-popups-to-escape-sandbox";

const REMOTE_ATTRIBUTE = "data-ax-remote-src";

/* 시안 `.scax-mail-html` 규칙을 iframe 문서용으로 — 값은 부모의 토큰을 그대로 옮겨 쓴다(아래 TOKENS). */
const FRAME_CSS = `
html,body{margin:0;padding:0;background:transparent}
body{font-family:var(--frame-font);font-size:var(--scax-text-body2-size,15px);line-height:1.7;color:var(--scax-color-ink);overflow-wrap:anywhere}
p{margin:0 0 var(--scax-space-300)}
ul,ol{margin:0 0 var(--scax-space-300);padding-left:var(--scax-space-600)}
table{max-width:100%;border-collapse:collapse;font-size:var(--scax-text-label2-size)}
img{max-width:100%;height:auto}
a{color:var(--scax-color-info)}
blockquote{margin:0;padding:var(--scax-space-100) 0 var(--scax-space-100) var(--scax-space-400);border-left:3px solid var(--scax-color-line-strong);color:var(--scax-color-ink-neutral)}
details{margin-top:var(--scax-space-300)}
details>summary{display:inline-block;padding:var(--scax-space-050) var(--scax-space-250);border:1px solid var(--scax-color-line);border-radius:var(--scax-radius-pill);background:var(--scax-color-surface);font-size:var(--scax-text-caption1-size);color:var(--scax-color-ink-neutral);cursor:pointer;list-style:none}
details>summary::-webkit-details-marker{display:none}
details[open]>summary{margin-bottom:var(--scax-space-300)}
img[${REMOTE_ATTRIBUTE}]:not([src]){display:inline-block;min-width:24px;min-height:24px;background:var(--scax-color-fill-weak)}
`;

const TOKENS = [
  "--scax-text-body2-size",
  "--scax-text-label2-size",
  "--scax-text-caption1-size",
  "--scax-color-ink",
  "--scax-color-ink-neutral",
  "--scax-color-info",
  "--scax-color-line",
  "--scax-color-line-strong",
  "--scax-color-surface",
  "--scax-color-fill-weak",
  "--scax-space-050",
  "--scax-space-100",
  "--scax-space-250",
  "--scax-space-300",
  "--scax-space-400",
  "--scax-space-600",
  "--scax-radius-pill",
];

function frameStyle(): string {
  const root = getComputedStyle(document.documentElement);
  const declarations = TOKENS.map((name) => {
    const value = root.getPropertyValue(name).trim();
    return value ? `${name}:${value};` : "";
  }).join("");
  const font = getComputedStyle(document.body).fontFamily;
  return `:root{${declarations}--frame-font:${font || "inherit"};}${FRAME_CSS}`;
}

export function MailFrame({ html, messageId }: { html: string; messageId: string }) {
  const frame = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState(120);
  const [blocked, setBlocked] = useState(0);
  const [shown, setShown] = useState(false);

  const measure = useCallback(() => {
    const doc = frame.current?.contentDocument;
    if (!doc?.documentElement) return;
    const next = Math.max(doc.documentElement.scrollHeight, doc.body?.scrollHeight ?? 0);
    if (next > 0) setHeight(next);
  }, []);

  const prepare = useCallback(() => {
    const doc = frame.current?.contentDocument;
    if (!doc?.head || !doc.body) return;
    if (!doc.getElementById("ax-frame-style")) {
      const style = doc.createElement("style");
      style.id = "ax-frame-style";
      style.textContent = frameStyle();
      doc.head.appendChild(style);
    }
    setBlocked(doc.querySelectorAll(`img[${REMOTE_ATTRIBUTE}]:not([src])`).length);
    /* 링크 — iframe 안에서 열지 않고 부모가 연다 */
    doc.addEventListener("click", (event) => {
      const target = event.target as Element | null;
      const anchor = target?.closest?.("a[href]");
      if (!anchor) return;
      const href = anchor.getAttribute("href") ?? "";
      if (!href || href.startsWith("#")) return;
      event.preventDefault();
      void openLink(href);
    });
    /* 인용 접기·그림이 실리면 높이가 바뀐다 */
    doc.addEventListener("toggle", measure, true);
    doc.querySelectorAll("img").forEach((image) => image.addEventListener("load", measure));
    measure();
  }, [measure]);

  useEffect(() => {
    setShown(false);
    setBlocked(0);
  }, [html]);

  /* 폭이 바뀌면 글이 다시 흘러 높이가 바뀐다 */
  useEffect(() => {
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [measure]);

  const showImages = () => {
    const doc = frame.current?.contentDocument;
    if (!doc) return;
    doc.querySelectorAll<HTMLImageElement>(`img[${REMOTE_ATTRIBUTE}]`).forEach((image) => {
      const remote = image.getAttribute(REMOTE_ATTRIBUTE);
      if (!remote || image.getAttribute("src")) return;
      image.addEventListener("load", measure);
      image.setAttribute("src", inboxRemoteImageUrl(messageId, remote));
    });
    setShown(true);
  };

  return (
    <>
      {blocked > 0 && !shown ? (
        <div className="scax-inbox-notice" role="status">
          <span className="scax-inbox-notice__text">{copy.remoteBlocked(blocked)}</span>
          <Button label={copy.remoteShow} onClick={showImages} size="sm" tone="neutral" variant="outlined" />
        </div>
      ) : null}
      <iframe
        className="scax-mail-frame"
        onLoad={prepare}
        ref={frame}
        sandbox={MAIL_SANDBOX}
        srcDoc={html}
        style={{ height }}
        title={copy.mailFrame}
      />
    </>
  );
}
