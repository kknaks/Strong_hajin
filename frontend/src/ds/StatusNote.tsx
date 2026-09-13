import type { ReactNode } from "react";

/**
 * 지금 무슨 일이 벌어지는지 한 줄로 말하는 자리 — 화면을 막지 않고 곁에 선다.
 *
 * 디자인 시스템 v2 에 이 부품이 없다. `.error-banner` 는 화면 폭을 먹는 띠라 「연결하는 중」 같은 말을
 * 담기에 너무 크고, `Toast` 는 4초 뒤 사라져 «지금 상태»를 계속 말하지 못한다.
 * 실패는 읽던 자리를 끊지 않고 `role="status"` 로만 알린다 — 이 줄은 조작이 아니다.
 */
export function StatusNote({ tone = "muted", children }: { tone?: "muted" | "danger"; children: ReactNode }) {
  return (
    <span className={tone === "danger" ? "status-note danger" : "status-note"} role="status">
      {children}
    </span>
  );
}
