/**
 * `src/styles/index.css` 를 한 벌로 펼친다.
 *
 * 왜 필요한가: `cfg.cssEntry` 의 내용은 `_ds_bundle.css` 에 **그대로 이어 붙는다**
 * (`package-build.mjs` 의 `appendFileSync`). 그런데 `index.css` 는 값을 안 갖고
 * `@import "./x.css"` 14줄만 가진 묶음 파일이라, 그대로 가면 번들 뿌리에서 그 14개가
 * 하나도 안 풀린다 (`[CSS_IMPORT_MISSING]` ×14).
 *
 * 그래서 빌드 직전에 import 순서를 지켜 한 파일로 펼쳐 둔다. **앱 코드는 건드리지 않는다** —
 * 산출물은 `frontend/.ds-styles-flat.css` 하나뿐이고 gitignore 된다.
 *
 * `url()` 은 손대지 않는다. `fonts.css` 의 `/fonts/*.woff2` 절대 경로는 컨버터의
 * `rewriteBundleFontFaces` 가 basename 으로 `./fonts/` 사본에 다시 물려 준다.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";

const ENTRY = "frontend/src/styles/index.css";
const OUT = "frontend/.ds-styles-flat.css";

const seen = new Set();
const chunks = [];

function inline(file) {
  const abs = resolve(file);
  if (seen.has(abs)) return;          // 같은 파일을 두 번 싣지 않는다
  seen.add(abs);
  const dir = dirname(abs);
  const text = readFileSync(abs, "utf8");
  let cursor = 0;
  // 문자열 안의 @import 를 잘못 잡지 않도록 줄 머리의 @import 만 본다.
  // 줄 끝 주석(`@import "./x.css"; /* ... */`)이 붙은 줄도 잡는다.
  const rx = /^[ \t]*@import\s+(?:url\(\s*)?["']([^"']+)["']\s*\)?\s*;[ \t]*(?:\/\*[^\n]*?\*\/[ \t]*)?$/gm;
  for (const m of text.matchAll(rx)) {
    chunks.push(text.slice(cursor, m.index));
    cursor = m.index + m[0].length;
    const spec = m[1];
    if (/^(?:https?:)?\/\//.test(spec)) {
      chunks.push(m[0]);              // 원격 @import 는 그대로 둔다
      continue;
    }
    chunks.push(`\n/* ── ${spec} (${ENTRY} 에서 펼침) ── */\n`);
    inline(resolve(dir, spec));
  }
  chunks.push(text.slice(cursor));
}

inline(ENTRY);

/*
 * 뿌리 글꼴 한 줄을 덧댄다 — **앱 CSS 에는 이 줄이 없다.**
 *
 * 바퀴 2 가 원본 DS 의 전역 리셋 7줄을 잘라냈고(`shell.css` 머리 주석), 바퀴 9-B 가 구
 * `styles.css` 에서 여덟 줄만 되살렸는데 그 안에 `body{font-family:…}` 가 **빠졌다.**
 * 그런데 `.scax-button`·`.scax-badge`·`.scax-chip`·`.scax-table`·`.scax-modal__title` 등
 * 글자를 내는 클래스 대부분이 font-family 를 스스로 걸지 않고 body 상속에 기댄다.
 * 그래서 이 번들은 브라우저 기본 글꼴(Times)로 렌더된다 — 실측했다:
 *   `getComputedStyle(document.body).fontFamily === "Times"`.
 *
 * `--font-ui` 는 `typography.css` 가 정의하고 있으므로 여기서는 **그 값을 쓰기만** 한다.
 * 새 값을 만들지 않는다. 앱 코드는 건드리지 않는다 — 이 줄은 번들 산출물에만 있다.
 *
 * ⚠ 이것은 **앱에도 있는 버그**다(앱도 같은 이유로 기본 글꼴로 떨어진다). 앱 쪽 수정은
 *   `src/styles/shell.css` 의 전역 element 구획이 맡아야 한다 — 이 스크립트의 일이 아니다.
 */
chunks.push(`
/* ── design-sync: 뿌리 글꼴 (앱 CSS 에 없는 줄 — flatten-css.mjs 가 덧댄다) ── */
body{font-family:var(--font-ui);color:var(--scax-color-ink);font-size:var(--scax-text-label1-size);line-height:var(--scax-text-label1-lh);letter-spacing:var(--scax-text-label1-ls)}
`);

const out = chunks.join("");
writeFileSync(OUT, out);
console.error(`  flatten-css: ${seen.size} files → ${OUT} (${(out.length / 1024) | 0} KB)`);
