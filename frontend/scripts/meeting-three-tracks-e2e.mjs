import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { chromium } from "@playwright/test";

import { loginAs, pollFor } from "./e2e-helpers.mjs";

/**
 * 회의록 **세 벌**을 실물 음성으로 끝까지 밟는다 (SPEC-004 v0.5.1 §4.0 · §4.2-6 · §8).
 *
 * 흉내 내는 것은 마이크 하나뿐이다 — Chrome 의 fake device 가 **실제 사람 목소리 wav** 를 물린다.
 * 그 뒤는 전부 실물이다: provider 가 받아 적고, 회의 중 배치가 AI 벌을 세우고, 종료 합성이 최종 벌을 짓는다.
 *
 * `meeting-live-transcript-e2e.mjs` 를 본떴지만 **단언은 세 벌 축으로 다시 썼다.** 그 하네스는
 * 한 벌 시절의 `recordings[].raw_transcript` · `summaries[kind=final]` 과 옛 UI(「녹음 시작」·
 * 「회의 목록」 탭)를 본다 — 지금 화면·계약에는 그 자리가 없다.
 *
 * ⚠ **음원은 실제 회의 녹음이다.** 이 스크립트는 전사된 «말» 을 로그에 옮기지 않는다 —
 * 세는 것은 길이와 개수뿐이다. 파일을 옮기거나 커밋하지 않는다.
 */

const frontendUrl = process.env.SCAX_E2E_URL ?? "http://localhost:5173";
const account = process.env.SCAX_E2E_ACCOUNT ?? "mina";
const wavPath = process.env.SCAX_E2E_WAV;

/*
 * 회의 중 AI 벌이 서려면 배치가 최소 한 번은 돌아야 한다. 배치 트리거는 셋이고(§7.1)
 * 그중 «시간» 트리거가 `BATCH_MAX_WAIT_SECONDS = 90` 이다 (`modules/meetings/batch.py:32`).
 * 글자 트리거(`BATCH_CHARS = 600`)가 먼저 걸리는 것이 보통이지만 **느린 쪽을 기준으로 잡는다** —
 * 90 × 1.5 = 135 가 바닥이고 여유를 둬 180 을 기본으로 둔다. 음원을 다 흘릴 필요는 없다.
 */
const streamSeconds = Number(process.env.SCAX_E2E_STREAM_SECONDS ?? 180);

/* 이 검사의 목적이 「보는 것」이라 **기본은 창을 띄운다.** `SCAX_E2E_HEADED=0` 이면 headless 다.
   `slowMo` 는 클릭이 순식간에 지나가 무엇을 하는지 안 보이는 것을 막는다. */
const headed = process.env.SCAX_E2E_HEADED !== "0";
const slowMo = Number(process.env.SCAX_E2E_SLOWMO ?? (headed ? 250 : 0));
/** 단언이 끝난 뒤 창을 열어 두는 시간 — 사람이 탭 둘을 직접 눌러 보는 자리다. */
const holdSeconds = Number(process.env.SCAX_E2E_HOLD ?? (headed ? 180 : 0));

if (!wavPath) throw new Error("SCAX_E2E_WAV 에 음원 경로를 주세요 (16bit PCM wav).");
if (!existsSync(wavPath)) throw new Error(`음원이 없습니다: ${wavPath}`);

/** 임시로 만든 변환본을 지우기 위해 들고 있는다 — 원본은 건드리지 않는다. */
let scratch = null;

/**
 * Chrome 의 fake device 가 이 파일을 그대로 먹는지 먼저 본다.
 *
 * 원본이 16kHz 인데 옛 하네스는 48kHz 로 만들어 물렸다. 어느 쪽이 맞는지 추측하지 않고 **재 본다** —
 * 브라우저를 띄워 `getUserMedia` 로 받은 소리의 진폭을 직접 읽는다. 0 이면 아무것도 안 들어온 것이다.
 */
async function audioReaches(file) {
  const probe = await chromium.launch({
    args: [
      "--use-fake-ui-for-media-stream",
      "--use-fake-device-for-media-stream",
      `--use-file-for-fake-audio-capture=${file}%noloop`,
    ],
    headless: true,
  });
  try {
    const page = await probe.newPage();
    /* `about:blank` 에는 `navigator.mediaDevices` 가 없다 — 보안 컨텍스트가 아니다.
       앱 오리진(http://localhost)은 보안 컨텍스트라 거기서 잰다. */
    await page.goto(frontendUrl, { waitUntil: "domcontentloaded" });
    return await page.evaluate(async () => {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1 } });
      const context = new AudioContext();
      const analyser = context.createAnalyser();
      context.createMediaStreamSource(stream).connect(analyser);
      const buffer = new Float32Array(analyser.fftSize);
      let peak = 0;
      const started = Date.now();
      while (Date.now() - started < 2500) {
        analyser.getFloatTimeDomainData(buffer);
        for (const sample of buffer) peak = Math.max(peak, Math.abs(sample));
        await new Promise((resolve) => setTimeout(resolve, 100));
      }
      for (const track of stream.getTracks()) track.stop();
      return peak;
    });
  } finally {
    await probe.close();
  }
}

/** 48kHz 모노 16bit 로 옮긴다 — 원본은 그대로 두고 임시본을 만든다(끝나면 지운다). */
function to48k(file) {
  scratch = mkdtempSync(join(tmpdir(), "scax-three-tracks-"));
  const out = join(scratch, "mic.wav");
  execFileSync("afconvert", ["-f", "WAVE", "-d", "LEI16@48000", "-c", "1", file, out]);
  return out;
}

const originalPeak = await audioReaches(wavPath);
let micFile = wavPath;
let converted = false;
if (originalPeak < 0.001) {
  micFile = to48k(wavPath);
  converted = true;
  const convertedPeak = await audioReaches(micFile);
  if (convertedPeak < 0.001) {
    throw new Error(`Chrome 이 16kHz 도 48kHz 도 먹지 않았다 (peak ${originalPeak} / ${convertedPeak}).`);
  }
}

/** 세 벌을 나눠 센다 — 화면이 아니라 **서버 응답**을 본다. 여기가 이 검사의 축이다. */
function tracksOf(detail) {
  const agendas = detail.agendas ?? [];
  const of = (track) => agendas.filter((agenda) => agenda.track === track);
  return { memo: of("memo"), ai: of("ai"), final: of("final"), all: agendas };
}

const browser = await chromium.launch({
  args: [
    "--use-fake-ui-for-media-stream",
    "--use-fake-device-for-media-stream",
    `--use-file-for-fake-audio-capture=${micFile}%noloop`,
  ],
  // 이 기계에 Chrome 이 없다 — Playwright 가 들고 있는 chromium 을 쓴다 (executablePath 를 주지 않는다).
  headless: !headed,
  slowMo,
});

const report = { audio: { path: wavPath, converted, peak: originalPeak }, stream_seconds: streamSeconds };

try {
  const page = await browser.newPage();
  page.on("pageerror", (error) => console.log(`[page error] ${error.message}`));
  await page.goto(frontendUrl, { waitUntil: "domcontentloaded" });
  await loginAs(page, account);

  const nav = page.getByRole("navigation", { name: "제품 탐색" });
  await nav.getByRole("button", { name: "회의" }).click();

  /* 「빠른 시작」 — 값을 묻지 않고 바로 여는 자리다. 돌아오는 것은 이미 「진행 중」인 회의이고,
     화면이 그 회의를 골라 상세를 연다. 그때 스트림이 붙고 마이크가 열린다.

     **회의 id 는 그 응답에서 집는다.** 목록에서 「진행 중」을 찾아 고르면 이 기계에서 «사람이
     지금 하고 있는 실제 회의» 를 집을 수 있다 — 남의 회의를 검사 대상으로 삼는 사고다. */
  const started = page.waitForResponse(
    (response) => response.url().includes("/api/meetings/quick-start") && response.request().method() === "POST",
    { timeout: 60_000 },
  );
  await page.getByRole("button", { name: /회의 시작/ }).first().click();
  const startedBody = await (await started).json();
  const meetingId = startedBody.meeting?.meeting_id;
  if (!meetingId) throw new Error(`빠른 시작 응답에 회의 id 가 없다: ${JSON.stringify(startedBody).slice(0, 200)}`);
  report.meeting_id = meetingId;
  await page.locator(".scax-live-bar").waitFor({ timeout: 60_000 });

  const detailOf = () =>
    page.evaluate(async (id) => (await fetch(`/api/meetings/${id}`)).json(), meetingId);

  /* ① 실물 음성에서 말을 건지는가 — **내용을 옮기지 않는다.** 줄 수와 글자 수만 센다. */
  const heard = await pollFor(
    page,
    async () => {
      const transcript = await page.evaluate(
        async (id) => (await fetch(`/api/meetings/${id}/transcript`)).json(),
        meetingId,
      );
      const items = transcript.items ?? [];
      return items.length > 0 ? items : null;
    },
    { timeout: 180_000, description: "실물 음성에서 확정 발화가 적재되는 것" },
  );
  report.live_transcript = {
    segments: heard.length,
    chars: heard.reduce((sum, item) => sum + (item.content ?? "").length, 0),
  };

  /* ② 회의 «중» 세 벌 — 배치가 돌 때까지 흘린 뒤 사람 벌과 AI 벌이 «따로» 서는지 본다. */
  const deadline = Date.now() + streamSeconds * 1000;
  const live = await pollFor(
    page,
    async () => {
      const tracks = tracksOf(await detailOf());
      if (tracks.ai.length > 0) return tracks;
      return Date.now() > deadline ? tracks : null;
    },
    { timeout: streamSeconds * 1000 + 120_000, description: "회의 중 AI 벌이 서는 것" },
  );
  report.during = {
    memo_agendas: live.memo.length,
    ai_agendas: live.ai.length,
    final_agendas: live.final.length,
    ai_lines: live.ai.reduce((sum, agenda) => sum + agenda.lines.length, 0),
    memo_lines: live.memo.reduce((sum, agenda) => sum + agenda.lines.length, 0),
    ai_provisional_todos: live.ai.reduce(
      (sum, agenda) => sum + agenda.todos.filter((todo) => todo.provisional).length,
      0,
    ),
  };

  const problems = [];
  if (live.ai.length === 0) problems.push("회의 중 AI 벌 안건이 서지 않았다 (배치가 안 돌았거나 결과가 비었다)");
  if (live.memo.length === 0) problems.push("사람 벌 안건이 없다 — 빠른 시작이 세우는 기본 안건이 사라졌다");
  // AI 벌 줄이 사람 벌 안건에 붙으면 계약 위반이다 (§4.2-9)
  for (const agenda of live.memo) {
    if (agenda.lines.some((line) => line.track === "ai")) problems.push(`사람 벌 안건 ${agenda.agenda_id} 에 AI 줄이 붙었다`);
  }
  for (const agenda of live.ai) {
    if (agenda.source !== null) problems.push(`AI 벌 안건 ${agenda.agenda_id} 의 source 가 null 이 아니다: ${agenda.source}`);
    if (agenda.concluded) problems.push(`AI 벌 안건 ${agenda.agenda_id} 에 결론 표시가 섰다 (§4.0-5 위반)`);
  }

  /* ③ 종료 → 합성. 최종 벌이 «새로» 서고 원본 두 벌은 그대로 남아야 한다 (§8-11 · D53). */
  await page.getByRole("button", { name: "회의 종료" }).click();
  const settled = await pollFor(
    page,
    async () => {
      const detail = await detailOf();
      const status = detail.meeting?.status;
      if (status === "failed") return { failed: true, detail };
      return status === "done" ? { failed: false, detail } : null;
    },
    { timeout: 600_000, description: "정리 중 → 종료(합성 완료)" },
  );
  report.after_status = settled.detail.meeting?.status;
  if (settled.failed) {
    report.failure_reason = settled.detail.meeting?.failure_reason ?? null;
    problems.push(`합성이 「실패」로 끝났다: ${report.failure_reason ?? "사유 없음"}`);
  }

  const after = tracksOf(settled.detail);
  report.after = {
    memo_agendas: after.memo.length,
    ai_agendas: after.ai.length,
    final_agendas: after.final.length,
    final_lines: after.final.reduce((sum, agenda) => sum + agenda.lines.length, 0),
    final_with_merged_from: after.final.filter((agenda) => (agenda.merged_from ?? []).length > 0).length,
    final_concluded: after.final.filter((agenda) => agenda.concluded).length,
    lines_with_from_lines: after.final.reduce(
      (sum, agenda) => sum + agenda.lines.filter((line) => (line.from_lines ?? []).length > 0).length,
      0,
    ),
  };

  if (after.final.length === 0 && !settled.failed) problems.push("종료 뒤 최종 벌 안건이 없다 — 합성이 아무것도 짓지 않았다");
  // 원본 두 벌은 최종 벌이 선 뒤에도 남는다 (§4.0-4 · D53)
  if (after.memo.length !== live.memo.length) problems.push(`사람 벌이 합성 뒤 달라졌다: ${live.memo.length} → ${after.memo.length}`);
  if (after.ai.length !== live.ai.length) problems.push(`AI 벌이 합성 뒤 달라졌다: ${live.ai.length} → ${after.ai.length}`);
  // 결론 표시는 최종 벌에만 (§4.0-5)
  for (const agenda of [...after.memo, ...after.ai]) {
    if (agenda.concluded) problems.push(`원본 벌 안건 ${agenda.agenda_id} 에 결론 표시가 섰다`);
  }
  if (after.final.length > 0 && report.after.final_with_merged_from === 0) {
    problems.push("최종 안건 어디에도 merged_from 이 차 있지 않다 — 계보가 비었다");
  }

  report.problems = problems;
  console.log(JSON.stringify(report, null, 2));

  /* 단언이 끝났다고 창을 닫지 않는다 — 사람이 「메모」/「AI 요약」 탭을 직접 눌러
     두 목록이 «다른지» 보는 자리다. 회의 상세에 머문 채로 기다린다. */
  if (holdSeconds > 0) {
    console.log(`창을 ${holdSeconds}초 열어 둔다 — 「메모」·「AI 요약」 탭을 눌러 목록이 갈리는지 보세요.`);
    await page.waitForTimeout(holdSeconds * 1000);
  }
  if (problems.length > 0) {
    console.log("meeting three tracks e2e FAILED");
    process.exitCode = 1;
  } else {
    console.log("meeting three tracks e2e passed");
  }
} finally {
  await browser.close();
  // 변환본은 남기지 않는다 — 실제 회의 녹음이다.
  if (scratch) rmSync(scratch, { recursive: true, force: true });
}
