import { chromium } from "@playwright/test";

import { loginAs, switchAccount } from "./e2e-helpers.mjs";

// The requester's visible path for a negotiated request:
// 내 업무 → 요청·배정 → 보낸 업무 → 상세 → 내용 고쳐 재상신, with the round history preserved.
const frontendUrl = process.env.SCAX_E2E_URL ?? "http://127.0.0.1:5176";
const title = `관계 IA 요청 ${Date.now()}`;
const revisedTitle = `${title} (조정 반영)`;
const condition = "9월 말까지면 가능합니다";

const browser = await chromium.launch({
  executablePath: process.env.PLAYWRIGHT_CHROME_PATH ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
});

try {
  const page = await browser.newPage();
  await page.goto(frontendUrl, { waitUntil: "domcontentloaded" });
  await loginAs(page, "mina");
  const navigation = page.getByRole("navigation", { name: "제품 탐색" });
  await navigation.getByRole("button", { name: "내 업무" }).click();

  // Mina requests work from Jiho.
  await page.getByRole("button", { name: "새 업무 추가" }).click();
  await page.getByRole("tab", { name: "요청", exact: true }).click();
  await page.getByLabel("요청할 업무").fill(title);
  await page.getByLabel("담당 후보").selectOption("jiho");
  await page.getByRole("button", { name: "업무 요청 보내기" }).click();

  // Jiho asks for a change instead of accepting.
  await switchAccount(page, "jiho");
  await navigation.getByRole("button", { name: "내 업무" }).click();
  const jihoCard = page.locator(".decision-panel .task-card", { hasText: title });
  await jihoCard.waitFor({ timeout: 20_000 });
  // The adjustment runs through the canonical judgement drawer.
  await jihoCard.getByRole("button", { name: "판단하기" }).click();
  const jihoDrawer = page.getByRole("dialog", { name: "판단 상세" });
  await jihoDrawer.getByRole("button", { name: "조정 요청" }).click();
  await jihoDrawer.getByLabel("조정 요청 사유").fill(condition);
  await jihoDrawer.getByRole("button", { name: "조정 요청 확정" }).click();
  await page.getByRole("dialog").waitFor({ state: "detached", timeout: 20_000 });

  // The requester finds it under the renamed tab, in the section for what they asked for.
  await switchAccount(page, "mina");
  await navigation.getByRole("button", { name: "내 업무" }).click();
  const relationTab = page.getByRole("tab", { name: "요청·배정" });
  await relationTab.waitFor({ timeout: 20_000 });
  if (await page.getByRole("tab", { name: "보낸 업무" }).count()) {
    throw new Error("the misleading 보낸 업무 tab is still present");
  }
  await relationTab.click();
  const sentSection = page.locator("section[aria-label='보낸 업무']");
  await sentSection.waitFor();
  const row = sentSection.locator("tr", { hasText: title });
  await row.waitFor({ timeout: 20_000 });
  // The request the assignee is negotiating must not be filed as something requested of me.
  if (await page.locator("section[aria-label='받은 업무']").locator("tr", { hasText: title }).count()) {
    throw new Error("a request the persona sent was also listed as requested of them");
  }
  await row.getByRole("button", { name: "상세보기" }).click();

  // The round history is preserved and the resubmit path is reachable from here.
  const drawer = page.getByRole("dialog", { name: "업무 요청 상세" });
  await drawer.getByText(condition).first().waitFor({ timeout: 10_000 });
  await drawer.getByRole("button", { name: "내용 고쳐 재상신" }).click();
  await drawer.locator("#revision-title").fill(revisedTitle);
  const resubmitted = page.waitForResponse(
    (response) => response.url().includes("/resubmit") && response.request().method() === "POST",
  );
  await drawer.getByRole("button", { name: "재상신" }).click();
  const resubmitResponse = await resubmitted;
  if (resubmitResponse.status() !== 200) throw new Error(`resubmit returned ${resubmitResponse.status()}`);

  // Same canonical request, new round: it stays in the same section under the new title.
  await page.getByRole("tab", { name: "요청·배정" }).click();
  await page.locator("section[aria-label='보낸 업무']").locator("tr", { hasText: revisedTitle }).waitFor({ timeout: 20_000 });
  const rounds = await page.evaluate(async (requestTitle) => {
    const list = await (await fetch("/api/work-requests", { headers: { "X-Demo-Persona": "mina" } })).json();
    const found = list.find((item) => item.title === requestTitle);
    if (!found) return null;
    const timeline = await (await fetch(`/api/work-requests/${found.request_id}/timeline`, { headers: { "X-Demo-Persona": "mina" } })).json();
    return { state: found.state, submissions: timeline.submissions.length, decisions: timeline.review_decisions.length };
  }, revisedTitle);
  if (!rounds || rounds.submissions < 2 || rounds.decisions < 1) {
    throw new Error(`resubmit did not preserve the round history: ${JSON.stringify(rounds)}`);
  }

  await page.screenshot({ path: "test-results/work-relations-e2e.png", fullPage: true });
  console.log(JSON.stringify({ result: "requester reached resubmit through 요청·배정 with history preserved", title: revisedTitle, ...rounds }));
} finally {
  await browser.close();
}
