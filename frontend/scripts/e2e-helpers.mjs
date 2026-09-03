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
