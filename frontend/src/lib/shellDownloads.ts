import { useSyncExternalStore } from "react";

import { hasShell, onShellDownload, type ShellDownloadResult } from "./shell";

/**
 * 데스크톱 앱의 **받는 중** 표시(WORK-012 2루프 E-2).
 *
 * 앱에서 받기를 누르면 셸이 이동을 가로채 별도 스레드로 받고, **끝날 때만** 사건(`strong-hajin:download` — `{ok, filename}`)을
 * 보낸다(`src-tauri/src/download.rs` `event_script` · `lib/shell.ts` `onShellDownload`). 누른 순간부터 끝까지 신호가 없어
 * 받고 있는지 알 수 없었다 — 그래서 **화면이 누른 순간 스스로** 「받는 중」 을 세우고, 셸 사건이 오면 걷는다(셸은 고치지 않는다).
 *
 * 사건에는 **주소가 없다**(이름과 성공 여부뿐). 그래서 어느 받기의 것인지 이렇게 가른다:
 * 1. 성공 사건의 파일 이름이 진행 중인 것의 이름과 같으면(셸이 붙이는 번호 「이름 (1).확장자」 도 같은 것으로 본다) 그것
 * 2. 아니면(실패 사건 — 이름이 없다 · 이름을 모름) **가장 오래된** 진행 중인 것
 * 사건이 영영 오지 않는 길도 있다(셸이 `inline` 응답을 «첨부 아님» 으로 보고 아무 알림도 내지 않을 때 등) — **60초** 뒤에는 걷는다.
 *
 * 웹(셸 없음)에서는 아무것도 세우지 않는다 — 브라우저가 제 다운로드 표시를 낸다(지금 그대로).
 */

export const SHELL_DOWNLOAD_TIMEOUT_MS = 60_000;

type Pending = { key: string; name: string; startedAt: number; timer: number };

let pending: Pending[] = [];
let listeners = new Set<() => void>();
let unsubscribe: (() => void) | null = null;

function emit() {
  listeners.forEach((listener) => listener());
}

function remove(key: string) {
  const found = pending.find((item) => item.key === key);
  if (!found) return;
  window.clearTimeout(found.timer);
  pending = pending.filter((item) => item !== found);
  emit();
}

/** 「이름 (3).pdf」 → 「이름.pdf」 — 셸이 같은 이름이 있으면 붙이는 번호를 뗀다(`download.rs` `numbered`). */
function baseName(name: string): string {
  return name.replace(/ \(\d+\)(?=(\.[^.]*)?$)/, "");
}

function settle(result: ShellDownloadResult) {
  if (pending.length === 0) return;
  const named = result.ok && result.filename ? pending.find((item) => item.name === result.filename || item.name === baseName(result.filename!)) : undefined;
  const target = named ?? pending.reduce((oldest, item) => (item.startedAt < oldest.startedAt ? item : oldest));
  remove(target.key);
}

function ensureSubscribed() {
  if (unsubscribe) return;
  unsubscribe = onShellDownload(settle);
}

/**
 * 받기를 시작했다 — 앱(셸 있음)일 때만 「받는 중」 을 세운다. `key` 는 그 받기의 주소(같은 첨부를 다시 누르면 같은 줄),
 * `name` 은 받는 파일 이름(셸 사건의 이름과 맞춰 본다). 이미 받는 중이면 다시 세우지 않는다.
 */
export function startShellDownload(key: string, name: string): void {
  if (!hasShell()) return;
  ensureSubscribed();
  if (pending.some((item) => item.key === key)) return;
  const timer = window.setTimeout(() => remove(key), SHELL_DOWNLOAD_TIMEOUT_MS);
  pending = [...pending, { key, name, startedAt: Date.now(), timer }];
  emit();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** 이 주소의 받기가 진행 중인가 — 첨부 부품이 스피너를 세우는 데 쓴다. */
export function useShellDownloading(key: string | null | undefined): boolean {
  return useSyncExternalStore(
    subscribe,
    () => Boolean(key) && pending.some((item) => item.key === key),
    () => false,
  );
}

/** 시험 전용 — 진행 중인 것과 구독을 비운다. */
export function resetShellDownloadsForTest(): void {
  pending.forEach((item) => window.clearTimeout(item.timer));
  pending = [];
  unsubscribe?.();
  unsubscribe = null;
  listeners = new Set();
}
