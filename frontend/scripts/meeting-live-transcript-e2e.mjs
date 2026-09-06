import { execFileSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { chromium } from "@playwright/test";

import { loginAs, pollFor } from "./e2e-helpers.mjs";

/**
 * Recording a meeting from the browser: microphone → Soniox realtime → live text → the file's own reading.
 *
 * Nothing here is simulated except the microphone itself, which Chrome fills from a wav of real speech. The temporary
 * key is issued by the server, the transcription is the provider's, and the acceptance is that the two readings stay
 * distinct: the live one is written while recording and is replaced by the authoritative `async_final` revision the
 * meeting worker makes from the uploaded audio.
 */
const frontendUrl = process.env.SCAX_E2E_URL ?? "http://127.0.0.1:5176";
const stamp = Date.now();
const meetingTitle = `실시간 전사 회의 ${stamp}`;
const SPOKEN = "안녕하세요. 오늘 회의를 시작하겠습니다. 지호가 계약서 초안을 내일까지 공유합니다. 감사합니다.";

/** Chrome's fake microphone plays a wav file; it has to be real speech for the provider to have anything to hear. */
function speechWav() {
  const directory = mkdtempSync(join(tmpdir(), "scax-live-"));
  const aiff = join(directory, "speech.aiff");
  const wav = join(directory, "speech.wav");
  execFileSync("say", ["-v", "Yuna", "-o", aiff, SPOKEN]);
  execFileSync("afconvert", ["-f", "WAVE", "-d", "LEI16@48000", "-c", "1", aiff, wav]);
  return wav;
}

const wav = speechWav();
const browser = await chromium.launch({
  args: [
    "--use-fake-ui-for-media-stream",
    "--use-fake-device-for-media-stream",
    `--use-file-for-fake-audio-capture=${wav}%noloop`,
  ],
  executablePath:
    process.env.PLAYWRIGHT_CHROME_PATH ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  headless: true,
});

try {
  const page = await browser.newPage();
  await page.goto(frontendUrl, { waitUntil: "domcontentloaded" });
  await loginAs(page, "mina");

  const meeting = await page.evaluate(async (title) => {
    const starts = new Date(Date.now() + 60_000);
    const response = await fetch("/api/meetings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        organization_id: "scax",
        title,
        starts_at: starts.toISOString(),
        ends_at: new Date(starts.getTime() + 30 * 60_000).toISOString(),
        visibility: "private",
        attendee_ids: ["mina"],
      }),
    });
    if (!response.ok) throw new Error(`meeting was not created: ${response.status} ${await response.text()}`);
    return response.json();
  }, meetingTitle);

  await page.getByRole("navigation", { name: "제품 탐색" }).getByRole("button", { name: "캘린더" }).click();
  await page.getByRole("tab", { name: "회의 목록" }).click();
  await page.locator(`[data-meeting-id="${meeting.meeting_id}"]`).getByRole("button", { name: "상세보기" }).click();
  await page.getByRole("heading", { name: meetingTitle }).waitFor();

  await page.getByRole("button", { name: "녹음 시작" }).click();
  const stream = page.getByLabel("실시간 대화록");
  await stream.waitFor();

  // The provider must settle real words while the recording is still open; a partial tail is not a transcript.
  const heard = await pollFor(
    page,
    async () => {
      const lines = await stream.locator("[data-live-segment]").allTextContents();
      return lines.length > 0 ? lines : null;
    },
    { timeout: 90_000, description: "live transcript lines settled from the fake microphone" },
  );
  const liveText = heard.join(" ");
  if (!/안녕하세요|회의|계약/.test(liveText)) {
    throw new Error(`The live transcript did not contain what was spoken: ${liveText}`);
  }

  // What the server actually stored while recording — a `realtime` revision, not the authoritative one.
  const duringRecording = await page.evaluate(async (meetingId) => {
    const detail = await (await fetch(`/api/meetings/${meetingId}`)).json();
    return detail.recordings?.[0]?.raw_transcript ?? null;
  }, meeting.meeting_id);
  if (duringRecording?.source_kind !== "realtime" || (duringRecording.segments ?? []).length === 0) {
    throw new Error(`Live segments were not persisted as a realtime revision: ${JSON.stringify(duringRecording)}`);
  }

  await page.getByRole("button", { name: "녹음 종료" }).click();
  await pollFor(page, async () => (await stream.count()) === 0, { timeout: 30_000, description: "the live view to close after stopping" });

  // The meeting worker reads the uploaded file itself; that reading supersedes the live one in the same place.
  const final = await pollFor(
    page,
    async () => {
      const detail = await page.evaluate(async (meetingId) => (await fetch(`/api/meetings/${meetingId}`)).json(), meeting.meeting_id);
      const raw = detail.recordings?.[0]?.raw_transcript;
      return raw && raw.source_kind === "async_final" && (raw.segments ?? []).length > 0 ? raw : null;
    },
    { timeout: 180_000, description: "the authoritative transcript made from the uploaded audio" },
  );
  if (final.revision <= duringRecording.revision) {
    throw new Error(`The file's reading did not supersede the live one: ${final.revision} <= ${duringRecording.revision}`);
  }

  // The rest of the pipeline runs on the file's reading, not the live one: refine, then summarize with evidence.
  const finished = await pollFor(
    page,
    async () => {
      const detail = await page.evaluate(async (meetingId) => (await fetch(`/api/meetings/${meetingId}`)).json(), meeting.meeting_id);
      const recording = detail.recordings?.[0];
      if (recording?.state === "failed") throw new Error("the meeting finalization pipeline failed after the upload");
      const summary = (detail.summaries ?? []).find((candidate) => candidate.kind === "final" && candidate.state === "completed");
      return recording?.state === "transcribed" && recording.refinement && summary ? { recording, summary } : null;
    },
    { timeout: 300_000, description: "the refined transcript and the final summary the file's reading produced" },
  );
  if (finished.summary.evidence.length === 0 || !finished.summary.body.trim()) {
    throw new Error(`The summary carries no evidence back to the transcript: ${JSON.stringify(finished.summary)}`);
  }

  console.log(
    JSON.stringify(
      {
        meeting: meeting.meeting_id,
        live_segments: duringRecording.segments.length,
        live_revision: duringRecording.revision,
        final_segments: final.segments.length,
        final_revision: final.revision,
        refined_segments: finished.recording.refinement.segments.length,
        summary_evidence: finished.summary.evidence.length,
      },
      null,
      2,
    ),
  );
  console.log("meeting live transcript e2e passed");
} finally {
  await browser.close();
}
