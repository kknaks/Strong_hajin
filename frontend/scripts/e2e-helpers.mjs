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

/** Picks from the product's accessible popover Select like a person does. */
export async function chooseOption(scope, label, optionName) {
  await scope.getByLabel(label, { exact: true }).click();
  await scope.getByRole("option", { name: optionName }).click();
}

async function useDesktopViewportWhenUnspecified(page) {
  const viewport = page.viewportSize();
  // Playwright's implicit 1280x720 viewport lands exactly on the responsive rail
  // breakpoint. Journeys that mean to exercise that breakpoint set an explicit
  // (usually taller) viewport; legacy desktop journeys leave the default alone.
  if (viewport?.width === 1280 && viewport.height === 720) {
    await page.setViewportSize({ width: 1440, height: 900 });
  }
}

/** Signs in through the ordinary email/password form as the given seeded account. */
export async function loginAs(page, accountId) {
  await useDesktopViewportWhenUnspecified(page);
  await page.getByLabel("이메일").fill(`${accountId}@scax.example`);
  await page.getByLabel("비밀번호").fill(DEMO_PASSWORD);
  await page.getByRole("button", { name: "로그인", exact: true }).click();
  await page.getByRole("navigation", { name: "제품 탐색" }).waitFor();
  const navigationScrim = page.locator(".rail-scrim");
  if (await navigationScrim.count() && await navigationScrim.isVisible()) await navigationScrim.click();
}

/** Signs in with the local demo shortcut — one press, and still a real sign-in through the same route. */
export async function quickLoginAs(page, accountId) {
  await useDesktopViewportWhenUnspecified(page);
  await page.locator(`[data-demo-account="${accountId}"]`).click();
  await page.getByRole("navigation", { name: "제품 탐색" }).waitFor();
  const navigationScrim = page.locator(".rail-scrim");
  if (await navigationScrim.count() && await navigationScrim.isVisible()) await navigationScrim.click();
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
  const axClose = page.locator(".ax-drawer").getByRole("button", { name: "닫기" });
  if (await axClose.count()) await axClose.click();
  const navigationToggle = page.getByRole("button", { name: "탐색 열기" });
  if (
    await navigationToggle.count()
    && await navigationToggle.isVisible()
    && await navigationToggle.getAttribute("aria-expanded") !== "true"
  ) await navigationToggle.click();
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
  // A drawer that just closed can still be animating the shell across the sidebar; wait for it to settle. The
  // acceptance suite runs every journey on one machine, so this waits as long as the other waits in the suite do —
  // a loaded machine is slower, not broken.
  const spot = await pollFor(page, at, { timeout: 20_000, description: "로그아웃 버튼이 눌릴 수 있게 되는 것" });
  await page.mouse.click(spot.x, spot.y);
  await page.getByLabel("이메일").waitFor();
}
