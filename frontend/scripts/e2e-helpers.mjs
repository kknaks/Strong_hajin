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
  await signOut(page);
  await loginAs(page, accountId);
}

/**
 * Signs out from wherever the page happens to be.
 *
 * Playwright scrolls an element into view before clicking it, and on a tall work surface that scroll moves the
 * sidebar out from under the pointer. So this checks that the button really is the topmost thing at its own
 * coordinates and then clicks those coordinates — a real click at a place we verified, not a forced one.
 */
export async function signOut(page) {
  const button = page.getByRole("button", { name: "로그아웃" });
  await button.waitFor();
  await page.evaluate(() => window.scrollTo(0, 0));
  const at = async () =>
    page.evaluate(() => {
      const target = [...document.querySelectorAll("button")].find((item) => item.textContent?.trim() === "로그아웃");
      if (!target) return null;
      const rect = target.getBoundingClientRect();
      const point = { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2 };
      return document.elementFromPoint(point.x, point.y) === target ? point : null;
    });
  // A drawer that just closed can still be animating the shell across the sidebar; wait for it to settle.
  const spot = await pollFor(page, at, { timeout: 10_000, description: "로그아웃 버튼이 눌릴 수 있게 되는 것" });
  await page.mouse.click(spot.x, spot.y);
  await page.getByLabel("이메일").waitFor();
}
