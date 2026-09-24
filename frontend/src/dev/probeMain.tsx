// WORK-006 Phase 1 계측 페이지의 엔트리. **제품 엔트리(`src/main.tsx`)와 갈라 둔다.**
// `probe.html` 만 이 파일을 참조하고, 제품 빌드(`npm run build`)의 입력은 `index.html` 하나라
// 계측 화면과 그 의존성은 제품 번들에 들어가지 않는다.
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import ProbePage from "./ProbePage";

createRoot(document.getElementById("probe-root")!).render(
  <StrictMode>
    <ProbePage />
  </StrictMode>,
);
