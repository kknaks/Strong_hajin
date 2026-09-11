import { chromium } from "@playwright/test";

import { loginAs, pollFor } from "./e2e-helpers.mjs";

const frontendUrl = process.env.SCAX_E2E_URL ?? "http://127.0.0.1:5176";
const title = `AX 요청 카드 ${Date.now()}`;
const browser = await chromium.launch({
  executablePath: process.env.PLAYWRIGHT_CHROME_PATH ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
});

try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
  await page.goto(frontendUrl, { waitUntil: "domcontentloaded" });
  await loginAs(page, "jiho");

  const conversationsLoaded = page.waitForResponse(
    (response) => response.url().endsWith("/api/conversations") && response.request().method() === "GET",
  );
  await page.getByRole("button", { name: "AX", exact: true }).click();
  await conversationsLoaded;
  await page.getByRole("button", { name: "새 AX 대화" }).click();
  await page.getByLabel("AX 메시지").fill(
    [
      "SCAX MCP의 task_assignment_candidates를 먼저 호출해 요청 가능한 사람을 확인하고,",
      `task_assign 도구로 제목 '${title}', 담당자 mina, 내용 '요청 카드 시각 검증', 기한 2026-09-30, 체크리스트 '요청 확인', '결과 공유'인 업무 요청 제안을 만들어줘.`,
      "답변으로만 제안하지 말고 도구를 호출한 뒤 내가 카드에서 확정할 때까지 기다려.",
    ].join(" "),
  );
  const created = page.waitForResponse(
    (response) => response.url().endsWith("/api/conversations") && response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "보내기" }).click();
  const conversation = await (await created).json();

  const pending = await pollFor(
    page,
    () => page.evaluate(async ({ conversationId, expectedTitle }) => {
      const response = await fetch(`/api/conversations/${conversationId}`);
      if (!response.ok) return null;
      const current = await response.json();
      return current.actions?.find(
        (item) => item.action_type === "task.assign" && item.state === "pending" && item.subject === expectedTitle,
      ) ?? null;
    }, { conversationId: conversation.conversation_id, expectedTitle: title }),
    { timeout: 120_000, description: "the pending editable task.assign Action" },
  );

  const preview = Object.fromEntries(pending.preview.map((row) => [row.id, row]));
  if (preview.assignee?.label !== "담당자" || !preview.assignee?.value.includes("민아")) {
    throw new Error(`request target was not projected as the assignee: ${JSON.stringify(preview.assignee)}`);
  }
  if (preview.requester?.label !== "요청자" || !preview.requester?.value.includes("지호")) {
    throw new Error(`Action owner was not projected as the requester: ${JSON.stringify(preview.requester)}`);
  }

  const card = page.locator(`.ax-action-card[data-action-id="${pending.action_id}"]`);
  await card.waitFor({ timeout: 20_000 });
  const target = page.getByLabel("요청 대상");
  await target.waitFor();
  const layout = await card.evaluate((element) => ({
    badges: [...element.querySelectorAll(".action-task-badges span")].map((badge) => badge.textContent?.trim()),
    buttonLabels: [...element.querySelectorAll(".action-task-actions button")].map((button) => button.textContent?.trim()),
    labels: [...element.querySelectorAll(".action-task-summary dt")].map((label) => label.textContent?.trim()),
    radius: getComputedStyle(element).borderRadius,
    width: element.getBoundingClientRect().width,
    values: [...element.querySelectorAll(".action-task-summary dd")].map((value) => value.textContent?.trim()),
    checklistItems: [...element.querySelectorAll(".action-task-summary-checklist li")].map((item) => item.textContent?.trim()),
    summaryText: element.querySelector(".action-task-summary")?.textContent ?? "",
  }));
  const targetLayout = await target.evaluate((element) => ({
    height: element.getBoundingClientRect().height,
    text: element.textContent?.replace(/\s+/g, " ").trim(),
  }));
  if (
    JSON.stringify(layout.badges) !== JSON.stringify(["SC AX", "요청"])
    || JSON.stringify(layout.buttonLabels) !== JSON.stringify(["수정", "등록"])
    || !layout.labels.includes("담당자")
    || !layout.labels.includes("요청자")
    || !layout.labels.includes("기한")
    || layout.radius !== "14px"
    || layout.width !== 380
    || targetLayout.height !== 40
    || !targetLayout.text?.includes("민아")
    || targetLayout.text?.includes("(")
    || layout.values.some((value) => /\([^()]+\)$/.test(value ?? ""))
    || JSON.stringify(layout.checklistItems) !== JSON.stringify(["요청 확인", "결과 공유"])
    || layout.summaryText.includes("→")
    || await page.locator(".action-task-completion-note").count()
  ) {
    throw new Error(`request summary diverged from the accepted card: ${JSON.stringify({ layout, targetLayout })}`);
  }

  await page.screenshot({ path: "test-results/ax-task-request-summary-e2e.png", fullPage: true });
  await card.locator("xpath=ancestor::section[contains(@class, 'ax-turn')]").screenshot({
    path: "test-results/ax-task-request-summary-turn-e2e.png",
  });

  await card.getByRole("button", { name: "등록" }).click();
  await pollFor(
    page,
    () => page.evaluate(async ({ conversationId, actionId }) => {
      const response = await fetch(`/api/conversations/${conversationId}`);
      if (!response.ok) return null;
      const current = await response.json();
      return current.actions?.find(
        (item) => item.action_id === actionId && item.state === "approved" && item.result?.status === "pending",
      ) ?? null;
    }, { conversationId: conversation.conversation_id, actionId: pending.action_id }),
    { timeout: 30_000, description: "the completed task request receipt" },
  );
  await card.getByRole("button", { name: "취소" }).waitFor();
  const completedLayout = await card.evaluate((element) => ({
    badges: [...element.querySelectorAll(".action-task-badges span")].map((badge) => badge.textContent?.trim()),
    buttonLabels: [...element.querySelectorAll(".action-task-actions button")].map((button) => button.textContent?.trim()),
    width: element.getBoundingClientRect().width,
  }));
  const completedNote = page.locator(".action-task-completion-note.request");
  const noteText = (await completedNote.textContent())?.replace(/\s+/g, " ").trim();
  if (
    JSON.stringify(completedLayout.badges) !== JSON.stringify(["SC AX", "요청"])
    || JSON.stringify(completedLayout.buttonLabels) !== JSON.stringify(["취소", "요청내용 보기"])
    || completedLayout.width !== 380
    || !noteText?.includes("민아님에게 업무가 요청되었습니다.")
    || !noteText?.includes("상대방이 요청을 확인하기 전까지 취소할 수 있어요.")
    || await page.getByLabel("요청 대상").count()
  ) {
    throw new Error(`completed request diverged from the accepted card: ${JSON.stringify({ completedLayout, noteText })}`);
  }
  await card.locator("xpath=ancestor::section[contains(@class, 'ax-turn')]").screenshot({
    path: "test-results/ax-task-request-complete-turn-e2e.png",
  });

  await card.getByRole("button", { name: "취소" }).click();
  await page.locator(".toast").getByText("업무 요청을 취소했습니다.").waitFor();
  await pollFor(
    page,
    () => page.evaluate(async ({ conversationId, actionId }) => {
      const response = await fetch(`/api/conversations/${conversationId}`);
      if (!response.ok) return false;
      const current = await response.json();
      const action = current.actions?.find((item) => item.action_id === actionId);
      return action?.result?.status === "cancelled" && !(action.commands ?? []).length;
    }, { conversationId: conversation.conversation_id, actionId: pending.action_id }),
    { timeout: 30_000, description: "the cancelled request receipt" },
  );
  await completedNote.getByText("업무 요청을 취소했습니다.", { exact: true }).waitFor();
  if (await card.getByRole("button", { name: "취소" }).count()) {
    throw new Error("the cancel command remained visible after the request was cancelled");
  }
  const [sent, recipientInbox, recipientWork] = await page.evaluate(async (expectedTitle) => {
    const personaFetch = (path, persona) => fetch(path, { headers: { "X-Demo-Persona": persona } }).then((response) => response.json());
    const rows = await personaFetch("/api/task-assignments/sent", "jiho");
    const inbox = await personaFetch("/api/action-items", "mina");
    const work = await personaFetch("/api/my-work", "mina");
    return [
      rows.find((row) => row.task?.title === expectedTitle),
      inbox.filter((row) => row.kind === "task.assignment" && row.subject === expectedTitle),
      work.filter((row) => row.title === expectedTitle),
    ];
  }, title);
  if (sent?.status !== "cancelled" || sent?.task?.state !== "cancelled" || recipientInbox.length || recipientWork.length) {
    throw new Error(`cancel did not settle every assignment projection: ${JSON.stringify({ sent, recipientInbox, recipientWork })}`);
  }
  console.log(JSON.stringify({
    result: "Task request matches the before/after cards and requester cancellation settles the canonical assignment",
    action_id: pending.action_id,
  }));
} finally {
  await browser.close();
}
