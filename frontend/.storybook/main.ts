import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import type { StorybookConfig } from "@storybook/react-vite";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, "../..");

/**
 * 스토리는 새로 쓰지 않는다 — `.design-sync/previews/<Name>.tsx` 한 벌이 그대로 스토리다.
 *
 * 그 파일들은 claude.ai/design 에 올라가는 프리뷰 카드의 원본이기도 하다. 두 벌을 두면 반드시
 * 갈라지므로(한쪽에만 스토리를 더하는 일이 생긴다) 여기서는 같은 파일을 읽기만 한다.
 * design-sync 컨버터는 대문자로 시작하는 named export 만 카드 셀로 세므로(`emit.mjs`),
 * 각 파일이 CSF 를 위해 갖고 있는 `export default` 메타는 카드 쪽에서 무시된다.
 */
const config: StorybookConfig = {
  stories: ["../../.design-sync/previews/*.tsx"],
  framework: { name: "@storybook/react-vite", options: {} },
  // 기본 인덱서는 파일 이름이 `*.stories.*` 인 것만 스토리로 본다. 프리뷰 파일 이름은
  // design-sync 가 소유하므로(`previews/<Name>.tsx` 로 찾는다) 이름을 바꾸는 대신
  // 기본 CSF 인덱서를 그 디렉터리에도 걸어 준다 — 파싱 규칙은 그대로 CSF 다.
  experimental_indexers: (existing = []) => {
    const csf = existing.find((indexer) => indexer.test.test("Any.stories.tsx"));
    if (!csf) return existing;
    return [{ ...csf, test: /[\\/]\.design-sync[\\/]previews[\\/][^\\/]+\.tsx$/ }, ...existing];
  },
  viteFinal: (vite) => {
    // 프리뷰는 부품을 패키지 이름으로 부른다(design-sync 번들이 그렇게 푸는 이름).
    // npm 은 자기 자신을 설치하지 않으므로 여기서 디자인 부품 엔트리로 직접 돌려준다.
    // alias 는 객체와 배열 두 형태가 다 유효하다 — react-vite 프리셋이 배열로 react/jsx-runtime 을
    // 걸어 두므로 객체로 펼치면 그게 통째로 깨진다. 들어온 형태 그대로 얹는다.
    const entry = resolve(here, "../ds-entry.tsx");
    const nodeModules = resolve(here, "../node_modules");
    // 스토리 파일이 frontend/ 바깥(리포 뿌리의 .design-sync/)이라 node 해석이 frontend/node_modules
    // 까지 올라오지 못한다 — react·react-dom 을 서브경로까지 한 줄로 되돌린다.
    // (`react-markdown` 은 `react` 뒤가 `$`도 `/`도 아니라 걸리지 않는다.)
    const entries = [
      { find: "ax-workspace-frontend", replacement: entry },
      { find: /^(react|react-dom)($|\/.*)$/, replacement: `${nodeModules}/$1$2` },
    ];
    // alias 는 객체와 배열 두 형태가 다 유효한데, 정규식 find 는 배열에서만 산다
    // (객체 키로 넣으면 정규식이 문자열로 굳어 영영 매치되지 않는다). 배열로 맞춰 둔다.
    vite.resolve ??= {};
    const alias = vite.resolve.alias;
    const existing = Array.isArray(alias)
      ? alias
      : Object.entries(alias ?? {}).map(([find, replacement]) => ({ find, replacement: replacement as string }));
    vite.resolve.alias = [...entries, ...existing];
    // 스토리 파일이 frontend/ 바깥(리포 뿌리의 .design-sync/)에 있다.
    vite.server ??= {};
    vite.server.fs = { ...vite.server.fs, allow: [repoRoot] };
    return vite;
  },
};

export default config;
