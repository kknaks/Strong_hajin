import { chromium } from "@playwright/test";
import { readFile } from "node:fs/promises";

import { loginAs } from "./e2e-helpers.mjs";


const frontendUrl = process.env.SCAX_E2E_URL ?? "http://127.0.0.1:5176";
const provenance = JSON.parse(await readFile(new URL("../assets/assistant-character/provenance.json", import.meta.url), "utf8"));
const browser = await chromium.launch({
  executablePath: process.env.PLAYWRIGHT_CHROME_PATH ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
});

try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  await page.goto(frontendUrl, { waitUntil: "domcontentloaded" });
  await loginAs(page, "mina");

  const launcher = page.locator(".assistant-launcher");
  await launcher.waitFor();
  if (await launcher.getAttribute("data-assistant-state") !== "idle") {
    throw new Error(`new session invented a non-idle assistant state: ${await launcher.getAttribute("data-assistant-state")}`);
  }
  const pointerContract = await launcher.evaluate((element) => ({
    container: getComputedStyle(element).pointerEvents,
    character: getComputedStyle(element.querySelector(".assistant-launcher-character")).pointerEvents,
    bubble: getComputedStyle(element.querySelector(".assistant-launcher-bubble")).pointerEvents,
  }));
  if (pointerContract.container !== "none" || pointerContract.character !== "auto" || pointerContract.bubble !== "auto") {
    throw new Error(`launcher blocks the canvas outside its controls: ${JSON.stringify(pointerContract)}`);
  }
  const referenceBytes = await launcher.locator("img").evaluate(async (image) => {
    const response = await fetch(image.src, { cache: "reload" });
    return (await response.arrayBuffer()).byteLength;
  });
  if (referenceBytes > provenance.production_export.individual_transfer_budget_bytes) {
    throw new Error(`selected character exceeds transfer budget: ${referenceBytes}`);
  }

  const quickPrompt = launcher.getByRole("button", { name: "오늘 할 일을 정리해줘" });
  await quickPrompt.focus();
  await page.keyboard.press("Enter");
  const drawer = page.getByRole("complementary", { name: "AX 대화" });
  await drawer.waitFor();
  const composer = drawer.getByLabel("AX 메시지");
  if (await composer.inputValue() !== "오늘 할 일을 정리해줘") {
    throw new Error("quick prompt did not prefill the ordinary AX composer");
  }
  await drawer.getByRole("button", { name: "새 AX 대화" }).click();
  await page.waitForFunction(() => document.querySelector("#ax-message")?.getAttribute("placeholder") === "메시지를 입력해 주세요.");
  await drawer.getByRole("button", { name: "보내기" }).click();
  await drawer.getByRole("button", { name: "닫기" }).click();

  await page.locator('.assistant-launcher[data-assistant-state="working"]').waitFor({ timeout: 10_000 });
  await page.locator('.assistant-launcher[data-assistant-state="answer-ready"]').waitFor({ timeout: 120_000 });
  await page.screenshot({ path: "test-results/assistant-character-answer-ready-1280.png", fullPage: true });

  await page.getByRole("button", { name: "AX", exact: true }).click();
  await drawer.locator('.assistant-character.header[data-assistant-state="idle"]').waitFor();
  if (await drawer.locator(".assistant .assistant-character").count()) {
    throw new Error("assistant character was repeated beside an answer");
  }
  await page.screenshot({ path: "test-results/assistant-character-drawer-answer-1280.png", fullPage: true });
  await drawer.getByRole("button", { name: "닫기" }).click();
  await page.locator('.assistant-launcher[data-assistant-state="idle"]').waitFor();

  await page.emulateMedia({ reducedMotion: "reduce" });
  const animationName = await page.locator(".assistant-launcher .assistant-character").evaluate(
    (element) => getComputedStyle(element).animationName,
  );
  if (animationName !== "none") throw new Error(`reduced motion still animates the character: ${animationName}`);

  await page.setViewportSize({ width: 720, height: 900 });
  const bounds = await page.evaluate(() => {
    const canvas = document.querySelector(".canvas").getBoundingClientRect();
    const character = document.querySelector(".assistant-launcher-character").getBoundingClientRect();
    const bubble = document.querySelector(".assistant-launcher-bubble").getBoundingClientRect();
    return {
      canvas: { left: canvas.left, right: canvas.right },
      launcherLeft: Math.min(character.left, bubble.left),
      launcherRight: Math.max(character.right, bubble.right),
      documentWidth: document.documentElement.scrollWidth,
      viewportWidth: window.innerWidth,
    };
  });
  if (
    bounds.launcherLeft < bounds.canvas.left
    || bounds.launcherRight > bounds.canvas.right
    || bounds.documentWidth > bounds.viewportWidth
  ) {
    throw new Error(`720px launcher escaped the canvas safe area: ${JSON.stringify(bounds)}`);
  }
  await page.screenshot({ path: "test-results/assistant-character-idle-720.png", fullPage: true });

  await page.setViewportSize({ width: 1280, height: 900 });
  await page.emulateMedia({ reducedMotion: "no-preference" });
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Performance.enable");
  await cdp.send("Emulation.setCPUThrottlingRate", { rate: 4 });
  const performanceMetric = async (name) => {
    const metrics = await cdp.send("Performance.getMetrics");
    return metrics.metrics.find((metric) => metric.name === name)?.value ?? 0;
  };
  const heapBeforePicker = await performanceMetric("JSHeapUsedSize");

  await page.getByRole("button", { name: "탐색 열기" }).click();
  await page.getByRole("button", { name: "설정" }).click();
  const picker = page.getByRole("dialog", { name: "내 AX 캐릭터" });
  await picker.waitFor();
  await picker.locator("img.is-ready").first().waitFor();
  const catalogMeasurement = await picker.locator("img").evaluateAll(async (images) => {
    const uniqueUrls = [...new Set(images.map((image) => image.currentSrc || image.src))];
    const cacheBust = `q7-budget-${Date.now()}`;
    const started = performance.now();
    const decoded = await Promise.all(uniqueUrls.map((url, index) => new Promise((resolve, reject) => {
      const image = new Image();
      image.onload = () => image.decode().then(() => resolve({
        width: image.naturalWidth,
        height: image.naturalHeight,
        loadAndDecodeMs: performance.now() - started,
      })).catch(reject);
      image.onerror = () => reject(new Error(`failed to cold-load ${url}`));
      image.src = `${url}${url.includes("?") ? "&" : "?"}${cacheBust}-${index}`;
    })));
    const coldLoadAndDecodeMs = performance.now() - started;
    const bytes = await Promise.all(uniqueUrls.map(async (url) => {
      const response = await fetch(url, { cache: "reload" });
      if (!response.ok) throw new Error(`failed to measure ${url}: ${response.status}`);
      return (await response.arrayBuffer()).byteLength;
    }));
    return {
      assets: uniqueUrls.length,
      coldLoadAndDecodeMs,
      selectedColdLoadAndDecodeMs: decoded[0].loadAndDecodeMs,
      slowestColdLoadAndDecodeMs: Math.max(...decoded.map((image) => image.loadAndDecodeMs)),
      totalBytes: bytes.reduce((total, value) => total + value, 0),
      largestBytes: Math.max(...bytes),
      decodedRgbaBytes: decoded.reduce((total, image) => total + image.width * image.height * 4, 0),
    };
  });
  const heapAfterPicker = await performanceMetric("JSHeapUsedSize");
  const pickerHeapDeltaBytes = Math.max(0, heapAfterPicker - heapBeforePicker);
  await picker.getByRole("button", { name: "캐릭터 선택 닫기" }).click();

  const frameDeltas = await page.evaluate(() => new Promise((resolve) => {
    const samples = [];
    let previous;
    const tick = (now) => {
      if (previous !== undefined) samples.push(now - previous);
      previous = now;
      if (samples.length >= 90) resolve(samples);
      else requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }));
  const sortedFrameDeltas = [...frameDeltas].sort((left, right) => left - right);
  const frameP95Ms = sortedFrameDeltas[Math.floor(sortedFrameDeltas.length * 0.95)];
  const taskDurationBefore = await performanceMetric("TaskDuration");
  await page.waitForTimeout(2_000);
  const mainThreadTaskMsPerTwoSeconds = ((await performanceMetric("TaskDuration")) - taskDurationBefore) * 1_000;
  await cdp.send("Emulation.setCPUThrottlingRate", { rate: 1 });

  const budget = provenance.production_export;
  if (catalogMeasurement.assets !== provenance.assets.length) throw new Error(`measured ${catalogMeasurement.assets} catalog assets`);
  if (catalogMeasurement.totalBytes > budget.catalog_transfer_budget_bytes) {
    throw new Error(`catalog exceeds transfer budget: ${catalogMeasurement.totalBytes}`);
  }
  if (catalogMeasurement.largestBytes > budget.individual_transfer_budget_bytes) {
    throw new Error(`catalog entry exceeds transfer budget: ${catalogMeasurement.largestBytes}`);
  }
  if (catalogMeasurement.decodedRgbaBytes > budget.catalog_decoded_rgba_budget_bytes) {
    throw new Error(`catalog exceeds decoded RGBA budget: ${catalogMeasurement.decodedRgbaBytes}`);
  }
  if (catalogMeasurement.coldLoadAndDecodeMs > 2_000) {
    throw new Error(`4x CPU catalog cold-load/decode exceeds 2s: ${catalogMeasurement.coldLoadAndDecodeMs}`);
  }
  if (catalogMeasurement.selectedColdLoadAndDecodeMs > 800) {
    throw new Error(`4x CPU selected character cold-load/decode exceeds 800ms: ${catalogMeasurement.selectedColdLoadAndDecodeMs}`);
  }
  if (frameP95Ms > 40) throw new Error(`4x CPU frame p95 exceeds 40ms: ${frameP95Ms}`);
  if (pickerHeapDeltaBytes > 8 * 1024 * 1024) throw new Error(`picker JS heap delta exceeds 8MiB: ${pickerHeapDeltaBytes}`);
  if (mainThreadTaskMsPerTwoSeconds > 300) {
    throw new Error(`idle animation main-thread work exceeds 300ms/2s at 4x CPU: ${mainThreadTaskMsPerTwoSeconds}`);
  }

  const fallbackPage = await browser.newPage({ viewport: { width: 720, height: 900 } });
  await fallbackPage.route("**/*.avif", (route) => route.abort());
  await fallbackPage.goto(frontendUrl, { waitUntil: "domcontentloaded" });
  await loginAs(fallbackPage, "jiho");
  const fallbackLauncher = fallbackPage.locator(".assistant-launcher");
  await fallbackLauncher.locator(".assistant-character-fallback").waitFor();
  await fallbackPage.getByRole("button", { name: "AX", exact: true }).click();
  await fallbackPage.getByRole("complementary", { name: "AX 대화" }).waitFor();
  await fallbackPage.close();

  console.log(JSON.stringify({
    result: "assistant state, identity, quick prompt, safe area, reduced motion, fallback and low-end performance budgets verified",
    selected_asset_bytes: referenceBytes,
    catalog: catalogMeasurement,
    simulated_low_end_cpu_rate: 4,
    frame_p95_ms: frameP95Ms,
    picker_js_heap_delta_bytes: pickerHeapDeltaBytes,
    idle_main_thread_task_ms_per_2s: mainThreadTaskMsPerTwoSeconds,
    states: ["idle", "working", "answer-ready"],
    viewports: [1280, 720],
  }));
} finally {
  await browser.close();
}
