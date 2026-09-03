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


/** Logs in through the local login page as the given seeded account. */
export async function loginAs(page, accountId) {
  await page.getByRole("radio", { name: new RegExp(`^${accountLabel(accountId)}`) }).check();
  await page.getByRole("button", { name: "로그인", exact: true }).click();
  await page.getByRole("navigation", { name: "제품 탐색" }).waitFor();
}

/** Logs the current account out and logs in as another seeded account. */
export async function switchAccount(page, accountId) {
  await page.getByRole("button", { name: "로그아웃" }).click();
  await loginAs(page, accountId);
}

function accountLabel(accountId) {
  return { mina: "민아", jiho: "지호", sora: "소라", minseok: "민석" }[accountId] ?? accountId;
}
