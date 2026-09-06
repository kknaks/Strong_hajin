export async function pollFor(page, probe, { timeout = 30_000, description }) {
  const deadline = Date.now() + timeout;
  let lastResult = null;

  while (Date.now() < deadline) {
    lastResult = await probe();
    if (lastResult) return lastResult;
    await page.waitForTimeout(250);
  }

  throw new Error(`Timed out waiting for ${description}. Last observed result: ${JSON.stringify(lastResult)}`);
}


/** The password every seeded demo account shares; it exists only behind `make reset-demo`. */
export const DEMO_PASSWORD = "scax-demo-1234";

/** Signs in through the ordinary email/password form as the given seeded account. */
export async function loginAs(page, accountId) {
  await page.getByLabel("이메일").fill(`${accountId}@scax.example`);
  await page.getByLabel("비밀번호").fill(DEMO_PASSWORD);
  await page.getByRole("button", { name: "로그인", exact: true }).click();
  await page.getByRole("navigation", { name: "제품 탐색" }).waitFor();
}

/** Signs the current account out and signs in as another seeded account. */
export async function switchAccount(page, accountId) {
  await page.getByRole("button", { name: "로그아웃" }).click();
  await loginAs(page, accountId);
}
