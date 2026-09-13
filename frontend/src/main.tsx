import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles/index.css"; // 디자인 시스템 한 벌 — 바퀴 9-B 에서 구 styles.css 가 사라졌다

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
