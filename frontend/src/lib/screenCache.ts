import { useCallback, useState, type Dispatch, type SetStateAction } from "react";

/**
 * 화면 데이터 기억 — 탭을 옮겨 갔다 돌아올 때 **받아 둔 것을 먼저 보여 주고 뒤에서 갱신한다**
 * (WORK-008 Phase 2 · B-01).
 *
 * 탭 전환은 페이지를 언마운트하므로(`App.tsx` 의 surface 조건부 렌더) 화면 상태가 매번 비고, 진입마다
 * 스켈레톤이 번쩍였다. 그래서 **화면이 마지막으로 받은 값**을 키별로 이 모듈에 남겨 두고, 다시 마운트될 때
 * 그 값으로 시작한다. 서버 호출은 지금처럼 `lib/api.ts` 가 하고 진입 effect 도 그대로 돈다 — 응답이 오면
 * 같은 setter 로 갈아 끼우므로 **권한(envelope) 판단은 늘 갱신된 응답**을 따른다. 쓰기 뒤의 다시 읽기도
 * 같은 setter 를 지나므로 옛 값이 남지 않는다.
 *
 * **주인이 있을 때만 기억한다.** `App` 이 로그인한 사람(`scopeScreenCache`)을 정한다. 주인이 바뀌거나
 * 비면(로그아웃·세션 만료·사용자 전환) 통째로 버린다 — 다른 사람의 데이터가 비치면 안 된다.
 * 주인이 없으면(화면 단위 테스트처럼 `App` 밖에서 그린 화면) 아무것도 기억하지 않고 `useState` 와 같다.
 */

let owner: string | null = null;
const store = new Map<string, unknown>();
/**
 * 기억의 세대 (WORK-008 Phase 2 fix1 · 검수 FAIL-1). 주인이 바뀌거나 비울 때마다 오른다.
 *
 * 요청은 «시작한 세대»를 들고 나가고, 응답이 왔을 때 세대가 그대로일 때만 기억한다. A 가 띄운 요청이
 * A 로그아웃 → B 로그인 **뒤에** 도착해도 B 의 기억에 A 의 값이 들어가지 않는다.
 */
let epoch = 0;

/** 키 접두사(`:` 앞)마다 남기는 최대 칸 수 — 구간·조직·날짜처럼 키가 갈리는 화면이 끝없이 쌓지 않게 한다. */
export const SCREEN_CACHE_KEYS_PER_PREFIX = 20;

/** 지금 화면을 쓰는 사람을 정한다. 다른 사람(또는 없음)이면 기억해 둔 것을 전부 버린다. */
export function scopeScreenCache(personaId: string | null | undefined): void {
  const next = personaId ? personaId : null;
  if (next === owner) return;
  store.clear();
  owner = next;
  epoch += 1;
}

/** 기억해 둔 것을 전부 버린다 — 로그아웃 · 앱이 새로 설 때. 이전 세대의 늦은 응답도 이제 기억되지 않는다. */
export function forgetScreenCache(): void {
  store.clear();
  epoch += 1;
}

/** 지금 세대. 요청을 시작할 때 잡아 두고 응답을 기억할 때 `rememberScreenValue` 에 넘긴다. */
export function currentScreenEpoch(): number {
  return epoch;
}

/**
 * 이 마운트가 선 세대. 화면이 `rememberScreenValue` 를 직접 부를 때 넘긴다 — 주인이 바뀌면(로그아웃·다른 사람
 * 로그인) 화면이 내려갔다 새로 서므로, 마운트 때 잡은 세대가 곧 «요청을 시작한 세대»다.
 */
export function useScreenEpoch(): number {
  const [mountedAt] = useState(currentScreenEpoch);
  return mountedAt;
}

/** 이 키로 받아 둔 값이 있나. */
export function hasScreenValue(key: string): boolean {
  return owner !== null && store.has(key);
}

/** 이 키로 받아 둔 값. 없으면 `undefined`. */
export function recallScreenValue<T>(key: string): T | undefined {
  return owner !== null ? (store.get(key) as T | undefined) : undefined;
}

/**
 * 이 키로 값을 남긴다. 주인이 없거나, **값을 요청한 세대(`requestedAt`)가 지금 세대가 아니면** 남기지 않는다.
 * 같은 접두사의 칸이 상한을 넘으면 가장 오래 쓰지 않은 칸부터 버린다.
 */
export function rememberScreenValue<T>(key: string, value: T, requestedAt: number): void {
  if (owner === null || requestedAt !== epoch) return;
  store.delete(key); // 다시 넣어 «최근» 으로 옮긴다 — Map 은 넣은 순서를 지킨다.
  store.set(key, value);
  const separator = key.indexOf(":");
  if (separator < 0) return;
  const prefix = key.slice(0, separator + 1);
  const sameScreen = [...store.keys()].filter((candidate) => candidate.startsWith(prefix));
  for (const stale of sameScreen.slice(0, Math.max(0, sameScreen.length - SCREEN_CACHE_KEYS_PER_PREFIX))) store.delete(stale);
}

/**
 * `useState` 자리에 그대로 들어가는 훅. 받아 둔 값이 있으면 그것으로 시작하고, setter 로 넣은 값은
 * 다음 진입을 위해 남긴다. 세 번째 값은 **이번 마운트가 받아 둔 값으로 시작했나**다 — 화면은 이것으로
 * 「데이터가 하나도 없는 첫 진입」에만 스켈레톤을 띄운다.
 *
 * 초깃값(`initial`)은 남기지 않는다 — 아직 아무것도 받지 않은 빈 목록을 «받은 값»으로 기억하면 다음 진입이
 * 스켈레톤 대신 「비어 있음」을 그린다.
 *
 * 이 마운트가 선 세대를 잡아 둔다 — 주인이 바뀐 뒤(로그아웃·다른 사람 로그인) 늦게 도착한 응답이 이 setter 를
 * 불러도 새 주인의 기억에 들어가지 않는다 (FAIL-1).
 */
export function useRemembered<T>(key: string, initial: T): [T, Dispatch<SetStateAction<T>>, boolean] {
  const mountedAt = useScreenEpoch();
  const [recalled] = useState(() => hasScreenValue(key));
  const [value, setValue] = useState<T>(() => (recalled ? (recallScreenValue<T>(key) as T) : initial));
  const set = useCallback<Dispatch<SetStateAction<T>>>(
    (update) =>
      setValue((previous) => {
        const next = typeof update === "function" ? (update as (current: T) => T)(previous) : update;
        rememberScreenValue(key, next, mountedAt);
        return next;
      }),
    [key, mountedAt],
  );
  return [value, set, recalled];
}
