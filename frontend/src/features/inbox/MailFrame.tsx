import { useCallback, useEffect, useRef, useState } from "react";

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
 * - **원격 이미지는 처음부터 보이되 우리 이미지 프록시(`remote-image`)로만** 단다(사용자 결정 2026-10-06 · FE 수정 판 2) —
 *   Gmail 처럼 사용자 브라우저가 보낸 쪽 서버에 직접 붙지 않는다(IP 노출 없음 · SSRF 규칙은 서버가 지킨다 · N-5).
 *   CSP `img-src 'self' data:` 그대로 — 이미지 출처는 우리 프록시뿐이다. 프록시가 실패한 그림은 깨진 아이콘 대신 빈 자리(alt 글자).
 * - 글자 모양은 앱 토큰 값을 iframe 에 옮겨 단다(iframe 은 부모 CSS 를 물려받지 않는다) — 시안 `.scax-mail-html` 규칙 그대로.
 */

export const MAIL_SANDBOX = "allow-same-origin allow-popups allow-popups-to-escape-sandbox";

const REMOTE_ATTRIBUTE = "data-ax-remote-src";

/** 듣는 손을 이미 단 iframe 문서. */
const wired = new WeakSet<Document>();

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
.ax-img-missing{display:inline-block;color:var(--scax-color-ink-neutral);font-size:var(--scax-text-caption1-size)}
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
    /* 원격 이미지 — 처음부터 프록시 주소로 단다. 실패하면 깨진 아이콘 대신 alt 글자만 남긴다 */
    doc.querySelectorAll<HTMLImageElement>(`img[${REMOTE_ATTRIBUTE}]`).forEach((image) => {
      const remote = image.getAttribute(REMOTE_ATTRIBUTE);
      if (!remote || image.getAttribute("src")) return;
      image.addEventListener("load", measure);
      image.addEventListener("error", () => {
        const missing = doc.createElement("span");
        missing.className = "ax-img-missing";
        missing.textContent = image.getAttribute("alt") ?? "";
        image.replaceWith(missing);
        measure();
      });
      image.setAttribute("src", inboxRemoteImageUrl(messageId, remote));
    });
    /* 같은 문서에 load 가 두 번 와도 듣는 손은 한 벌만 — 링크가 두 번 열리지 않게 */
    if (wired.has(doc)) {
      measure();
      return;
    }
    wired.add(doc);
    /* 링크 — iframe 안에서 열지 않고 부모가 연다. 가운데 클릭(auxclick)도 같다(검수 W-6 — 데스크톱은 새 창을 막는다).
       열 수 없는 스킴(F-1)은 막기만 하고 열지 않는다. */
    const intercept = (event: MouseEvent) => {
      if (event.type === "auxclick" && event.button !== 1) return;
      const target = event.target as Element | null;
      const anchor = target?.closest?.("a[href]");
      if (!anchor) return;
      const href = anchor.getAttribute("href") ?? "";
      if (!href || href.startsWith("#")) return;
      event.preventDefault();
      void openLink(href);
    };
    doc.addEventListener("click", intercept);
    doc.addEventListener("auxclick", intercept);
    /* 인용 접기·그림이 실리면 높이가 바뀐다 */
    doc.addEventListener("toggle", measure, true);
    doc.querySelectorAll("img").forEach((image) => image.addEventListener("load", measure));
    measure();
  }, [measure, messageId]);

  /* 폭이 바뀌면 글이 다시 흘러 높이가 바뀐다 */
  useEffect(() => {
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [measure]);

  return (
    <iframe
      className="scax-mail-frame"
      onLoad={prepare}
      ref={frame}
      sandbox={MAIL_SANDBOX}
      srcDoc={html}
      style={{ height }}
      title={copy.mailFrame}
    />
  );
}
