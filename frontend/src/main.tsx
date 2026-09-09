import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { MinWidthNotice } from "./MinWidthNotice";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {/* 지원하지 않는 폭 안내는 로그인 전에도 떠야 해서 앱 바깥에 있다 (v2 15) */}
    <MinWidthNotice />
    <App />
  </StrictMode>,
);
