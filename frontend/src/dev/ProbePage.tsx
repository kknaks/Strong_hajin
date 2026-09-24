/**
 * WORK-006 Phase 1 — **계측 페이지**. 제품 화면이 아니다.
 *
 * Phase 2 가 재는 열 건을 **전부 조작할 수 있는** 조작판이다. 무엇을 어디서 재는지는
 * 각 패널 머리에 M 번호로 적어 둔다. 셸이 관측하는 것(네비게이션 훅·창 닫기 요청·연결 실패)은
 * **셸의 표준출력**으로 나간다 — 원격 문서에 이벤트 채널을 열면 그것이 다섯째 표면이 되고
 * I-2(커맨드 넷)가 흐려지기 때문이다. Phase 2 는 터미널 로그와 이 화면을 함께 기록한다.
 *
 * 제품 모듈을 import 하지 않는다. 스타일도 제품 디자인 시스템을 끌어오지 않는다 —
 * 이 페이지는 `probe.html` 엔트리에서만 뜨고 제품 번들에 들어가지 않는다.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  createGuardClient,
  hasShell,
  newSessionKey,
  tauriCommands,
  type GuardClient,
  type ShellCommands,
  type ShellInfo,
  type WakeState,
} from "./probeShell";
import {
  addChunk,
  BROWSER_RECORDING_CANDIDATES,
  CHUNK_MS,
  emptyStats,
  MEETING_LIVE_MIME,
  probeMimeSupport,
  type MimeProbe,
  type RecordingStats,
} from "./probeRecording";

type LogLine = { at: string; tag: string; text: string };

const box: React.CSSProperties = {
  border: "1px solid #d5d8e2",
  borderRadius: 8,
  padding: "12px 14px",
  marginBottom: 12,
  background: "#fff",
};
const head: React.CSSProperties = { margin: "0 0 8px", fontSize: 14, fontWeight: 700 };
const note: React.CSSProperties = { margin: "0 0 8px", fontSize: 12, color: "#5b6070", lineHeight: 1.6 };
const row: React.CSSProperties = { display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" };
const mono: React.CSSProperties = { fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", fontSize: 12 };

export default function ProbePage() {
  const shellPresent = useMemo(() => hasShell(), []);
  // `E-01` — 셸이 없으면 커맨드 객체를 아예 만들지 않는다. 호출할 길이 없어야 한다.
  const commands = useMemo<ShellCommands | null>(() => (shellPresent ? tauriCommands() : null), [shellPresent]);
  const guard = useMemo<GuardClient | null>(() => (commands ? createGuardClient(commands) : null), [commands]);

  const [log, setLog] = useState<LogLine[]>([]);
  const append = useCallback((tag: string, text: string) => {
    setLog((lines) => [{ at: new Date().toISOString(), tag, text }, ...lines].slice(0, 300));
  }, []);

  return (
    <div style={{ padding: 20, maxWidth: 980, margin: "0 auto", color: "#1d2030", background: "#f5f6fa", minHeight: "100vh" }}>
      <h1 style={{ fontSize: 18, margin: "0 0 4px" }}>SCAX 탐침 계측 — WORK-006 Phase 1</h1>
      <p style={note}>
        제품 화면이 아니다. Phase 2 의 실측 열 건을 조작하는 판이다. 셸 쪽 관측(네비게이션 훅 ·
        창 닫기 요청 · 연결 실패)은 <strong>셸을 띄운 터미널의 `[probe]` 로그</strong>에 남는다.
      </p>

      <ShellPanel present={shellPresent} commands={commands} onLog={append} />
      <WakeGuardPanel guard={guard} present={shellPresent} onLog={append} />
      <RecordingPanel guard={guard} onLog={append} />
      <NavigationPanel commands={commands} onLog={append} />
      <CookiePanel onLog={append} />
      <CloseGuardPanel onLog={append} />
      <LogPanel log={log} onClear={() => setLog([])} />
    </div>
  );
}

/** M-1 — 원격 https 문서에서 커맨드 왕복이 성립하는가. **Phase 2 중단 판정의 근거다.** */
function ShellPanel({
  present,
  commands,
  onLog,
}: {
  present: boolean;
  commands: ShellCommands | null;
  onLog: (tag: string, text: string) => void;
}) {
  const [info, setInfo] = useState<ShellInfo | null>(null);
  const [error, setError] = useState<string | null>(null);

  const ask = useCallback(async () => {
    if (!commands) return;
    try {
      const result = await commands.shellInfo();
      setInfo(result);
      setError(null);
      onLog("M-1", `shell_info 성공 ${JSON.stringify(result)}`);
    } catch (reason) {
      setInfo(null);
      setError(String(reason));
      // `E-14a` — 셸은 있는데 호출이 실패했다. 기능 부재와 다르다.
      onLog("M-1", `shell_info 실패(E-14a): ${String(reason)}`);
    }
  }, [commands, onLog]);

  useEffect(() => {
    // 제품의 순서와 같게 **화면이 뜨자마자 한 번** 묻는다. 주기적으로 묻지 않는다(I-3).
    void ask();
  }, [ask]);

  return (
    <section style={box}>
      <h2 style={head}>M-1 · 셸 존재와 커맨드 왕복</h2>
      <p style={note}>
        셸 전역 감지: <strong>{present ? "있음" : "없음(브라우저)"}</strong>.
        없으면 <strong>커맨드를 한 번도 부르지 않는다</strong> — 에러도 경고도 없어야 한다(`E-01` · AC-T30).
      </p>
      <div style={row}>
        <button onClick={ask} disabled={!commands}>
          shell_info 다시 부르기
        </button>
      </div>
      {info && <pre style={{ ...mono, marginTop: 8 }}>{JSON.stringify(info, null, 2)}</pre>}
      {error && <p style={{ ...mono, color: "#b3261e" }}>{error}</p>}
    </section>
  );
}

/** M-9 — 절전 방지가 실제로 자동 절전만 막는가. 관찰은 `pmset -g assertions` / `powercfg /requests`. */
function WakeGuardPanel({
  guard,
  present,
  onLog,
}: {
  guard: GuardClient | null;
  present: boolean;
  onLog: (tag: string, text: string) => void;
}) {
  const [session, setSession] = useState(() => newSessionKey());
  const [state, setState] = useState<WakeState | "—">("—");

  const call = useCallback(
    async (kind: "acquire" | "release", key: string) => {
      if (!guard) return;
      try {
        const result = kind === "acquire" ? await guard.acquire(key, "회의 녹음 중") : await guard.release(key);
        setState(result.state);
        onLog("M-9", `${kind}(${key}) → state=${result.state}`);
      } catch (reason) {
        // `E-14a`(획득) · `E-14b`(해제) — 해제 실패를 「녹음 계속」으로 뭉개지 않는다.
        onLog("M-9", `${kind}(${key}) 실패(${kind === "release" ? "E-14b" : "E-14a"}): ${String(reason)}`);
      }
    },
    [guard, onLog],
  );

  return (
    <section style={box}>
      <h2 style={head}>M-9 · 절전 방지 점유</h2>
      <p style={note}>
        관찰은 OS 쪽에서 한다 — macOS <code style={mono}>pmset -g assertions</code> ·
        Windows <code style={mono}>powercfg /requests</code>.
        관찰 시간은 설정된 자동 절전 대기시간 <code style={mono}>T</code> 에 대해
        <strong> max(3T, 30분) 이상</strong>(AC-T05~T07).
      </p>
      <div style={row}>
        <span style={mono}>session: {session}</span>
        <button onClick={() => setSession(newSessionKey())}>새 세션 키(회차 바꿈)</button>
      </div>
      <div style={{ ...row, marginTop: 8 }}>
        <button onClick={() => void call("acquire", session)} disabled={!present}>
          acquire
        </button>
        <button onClick={() => void call("acquire", session)} disabled={!present}>
          같은 키로 재요청(E-09 재무장)
        </button>
        <button onClick={() => void call("release", session)} disabled={!present}>
          release
        </button>
        <button onClick={() => void call("release", newSessionKey())} disabled={!present}>
          모르는 키로 release(E-08)
        </button>
        <strong>state: {state}</strong>
      </div>
    </section>
  );
}

/** M-2 · M-4 — 포맷 판정과 장시간 녹음, 그리고 마이크 권한 프롬프트. */
export function RecordingPanel({ guard, onLog }: { guard: GuardClient | null; onLog: (tag: string, text: string) => void }) {
  const [probe, setProbe] = useState<MimeProbe | null>(null);
  const [stats, setStats] = useState<RecordingStats | null>(null);
  const [micError, setMicError] = useState<string | null>(null);
  // 점유 쪽 문제는 마이크 문제와 **따로** 보인다. 합쳐 놓으면 기록이 거짓이 된다(W-2).
  const [guardIssue, setGuardIssue] = useState<string | null>(null);
  const running = useRef<{ recorder: MediaRecorder; stream: MediaStream; session: string } | null>(null);

  useEffect(() => {
    if (typeof MediaRecorder === "undefined") {
      onLog("M-2", "MediaRecorder 가 없다 — 이 환경에서는 녹음을 열 수 없다");
      return;
    }
    const result = probeMimeSupport((mime) => MediaRecorder.isTypeSupported(mime));
    setProbe(result);
    onLog("M-2", `isTypeSupported: ${JSON.stringify(result)}`);
  }, [onLog]);

  const start = useCallback(
    async (path: "meeting" | "browser") => {
      if (running.current) return;
      setMicError(null);
      setGuardIssue(null);
      const session = newSessionKey();

      // ── ① 마이크·레코더 구간. **여기서 난 실패만 `E-13` 이다.** ──────────────────────
      // 이 구간과 아래 점유 구간을 한 `try` 로 묶으면 IPC 실패가 「마이크가 안 열렸다」로
      // 기록된다. 그러면 Phase 2 의 M-2·M-4 기록이 거짓이 되고 **중단 판정이 잘못 열린다** —
      // `E-13`(마이크가 안 열린다)과 `E-14a`(셸은 있는데 커맨드 호출이 실패)는
      // SPEC-006 §4 Case Matrix 에서 **웹이 하는 일도 보이는 것도 다른 줄**이다.
      let opened: { recorder: MediaRecorder; stream: MediaStream };
      try {
        // M-4 — 여기서 OS 마이크 프롬프트가 뜨는지 본다. 제품과 같은 제약을 건다.
        const stream = await navigator.mediaDevices.getUserMedia(
          path === "meeting" ? { audio: { channelCount: 1, sampleRate: 16000 } } : { audio: true },
        );
        const requested =
          path === "meeting" ? MEETING_LIVE_MIME : (probe?.browserChosen ?? undefined);
        let recorder: MediaRecorder;
        try {
          recorder = new MediaRecorder(stream, requested ? { mimeType: requested } : undefined);
        } catch (reason) {
          for (const track of stream.getTracks()) track.stop();
          throw reason;
        }
        let current = emptyStats(recorder.mimeType, performance.now());
        setStats(current);
        recorder.ondataavailable = (event) => {
          if (!event.data || event.data.size === 0) return;
          current = addChunk(current, event.data.size, performance.now());
          setStats(current);
        };
        recorder.start(CHUNK_MS);
        opened = { recorder, stream };
        onLog("M-2", `녹음 시작 path=${path} requested=${requested ?? "(기본)"} actual=${recorder.mimeType}`);
      } catch (reason) {
        // `E-13` · L-03 — 권한 거부와 포맷 미지원을 제품이 구분하지 않는다.
        // **점유를 걸지 않는다.** 녹음이 없는데 기기를 깨워 둘 이유가 없다.
        setMicError(String(reason));
        onLog("M-4", `마이크 열기 실패(E-13) — 점유를 걸지 않았다: ${String(reason)}`);
        return;
      }

      // 마이크가 **실제로** 열렸다. 이 아래에서 무엇이 실패하든 녹음은 이미 돌고 있다.
      running.current = { ...opened, session };

      // ── ② 점유 구간. **여기서 난 실패는 `E-14a` 이고 녹음을 멈추지 않는다.** ─────────
      // L-01 / L-07 — 마이크가 «실제로» 열린 바로 그 시점에 건다(연결 시도·준비 신호가 아니다).
      try {
        const result = await guard?.acquire(session, "회의 녹음 중");
        if (result) {
          onLog("M-9", `녹음 시작에 맞춘 acquire → state=${result.state}`);
          // `degraded` 는 실패가 아니라 **점유는 섰고 OS 에 못 걸린** 상태다 — 녹음은 계속된다.
          if (result.state === "degraded") setGuardIssue("degraded — 절전 방지를 걸지 못했다(E-02/E-03). 녹음은 계속된다.");
        }
      } catch (reason) {
        // `E-14a` — **마이크 실패가 아니다.** 녹음은 계속하고, 실패한 것은 커맨드 호출이다.
        setGuardIssue(`E-14a — wake_guard_acquire 호출 실패: ${String(reason)}`);
        onLog("M-9", `wake_guard_acquire 실패(E-14a) — 마이크는 열려 있고 녹음은 계속한다: ${String(reason)}`);
      }
    },
    [guard, onLog, probe],
  );

  const stop = useCallback(async () => {
    const active = running.current;
    if (!active) return;
    running.current = null;
    try {
      if (active.recorder.state !== "inactive") active.recorder.stop();
    } catch {
      // 이미 끝난 장치다 — 여기서 던지면 해제를 못 부른다.
    }
    for (const track of active.stream.getTracks()) track.stop();
    onLog("M-2", "녹음 종료 — 마이크를 닫았다");
    // **L-02 / L-08 — 녹음이 «실제로» 끝난 자리에서 푼다.**
    const result = await guard?.release(active.session);
    if (result) onLog("M-9", `녹음 종료에 맞춘 release → state=${result.state}`);
  }, [guard, onLog]);

  return (
    <section style={box}>
      <h2 style={head}>M-2 · M-4 — 두 녹음 경로의 포맷과 장시간 녹음</h2>
      <p style={note}>
        <strong>두 경로를 뭉치지 않는다.</strong> 회의 라이브는 단일 MIME 하드코딩(후보 없음)이라
        여기가 닫히면 <strong>중단 판정 후보</strong>이고, 브라우저 인터랙션은 후보 셋이라
        내려앉는 것이 중단 사유가 아니다. <code style={mono}>isTypeSupported</code> 가 참인 것으로
        닫지 않는다 — <strong>장시간 녹음</strong>과 <strong>서버 수용</strong>을 따로 본다.
      </p>
      {probe && (
        <table style={{ ...mono, borderCollapse: "collapse", marginBottom: 8 }}>
          <tbody>
            <tr>
              <td style={{ paddingRight: 12 }}>회의 라이브 (단일)</td>
              <td style={{ paddingRight: 12 }}>{probe.meetingLive.mime}</td>
              <td>{probe.meetingLive.supported ? "지원" : "미지원"}</td>
            </tr>
            {probe.browserCandidates.map((candidate, index) => (
              <tr key={candidate.mime}>
                <td style={{ paddingRight: 12 }}>브라우저 후보 {index + 1}</td>
                <td style={{ paddingRight: 12 }}>{candidate.mime}</td>
                <td>{candidate.supported ? "지원" : "미지원"}</td>
              </tr>
            ))}
            <tr>
              <td style={{ paddingRight: 12 }}>브라우저 낙착</td>
              <td colSpan={2}>{probe.browserChosen ?? "없음 — 후보 셋 전부 실패"}</td>
            </tr>
          </tbody>
        </table>
      )}
      <div style={row}>
        <button onClick={() => void start("meeting")}>회의 라이브 경로로 녹음 시작</button>
        <button onClick={() => void start("browser")}>브라우저 경로로 녹음 시작</button>
        <button onClick={() => void stop()}>녹음 종료</button>
      </div>
      {micError && (
        <p style={{ ...mono, color: "#b3261e" }}>
          <strong>마이크 실패(E-13)</strong> — 점유를 걸지 않았다: {micError}
        </p>
      )}
      {guardIssue && (
        <p style={{ ...mono, color: "#8a5a00" }}>
          <strong>점유 문제</strong> — 마이크는 열려 있고 녹음은 계속된다: {guardIssue}
        </p>
      )}
      {stats && (
        <pre style={{ ...mono, marginTop: 8 }}>
          {[
            `actualMime : ${stats.actualMime}`,
            `chunks     : ${stats.chunks}`,
            `bytes      : ${stats.bytes}`,
            `maxGapMs   : ${Math.round(stats.maxGapMs)}  (청크 간격 ${CHUNK_MS}ms 대비)`,
            `elapsedSec : ${stats.lastChunkAt ? Math.round((stats.lastChunkAt - stats.startedAt) / 1000) : 0}`,
          ].join("\n")}
        </pre>
      )}
    </section>
  );
}

/** M-3 · M-8 — 네비게이션 훅이 무엇에 발화하는가, 외부 링크가 무엇을 하는가. */
function NavigationPanel({
  commands,
  onLog,
}: {
  commands: ShellCommands | null;
  onLog: (tag: string, text: string) => void;
}) {
  const [screen, setScreen] = useState("A");
  const external = "https://example.com/";

  return (
    <section style={box}>
      <h2 style={head}>M-3 · M-8 — 네비게이션 경계와 외부 링크</h2>
      <p style={note}>
        <strong>훅이 발화하는가를 재는 것이지 점유가 남아 있는가를 재는 것이 아니다</strong>(AC-T17).
        셸 로그에서 <code style={mono}>[probe][nav]</code>·<code style={mono}>[probe][load]</code> 를 본다.
        위 넷(화면 전환 · replaceState · pushState · 해시)은 <strong>문서가 바뀌지 않으므로</strong>
        해제 트리거가 아니어야 하고(L-11), 아래 둘(재로드 · 다른 문서)은 <strong>L-09</strong> 다.
      </p>
      <div style={row}>
        <button
          onClick={() => {
            setScreen((value) => (value === "A" ? "B" : "A"));
            onLog("M-3", "앱 안 화면 전환(문서 그대로) — L-10/L-11");
          }}
        >
          앱 안 화면 전환 (지금: {screen})
        </button>
        <button
          onClick={() => {
            window.history.replaceState({}, "", `${location.pathname}?r=${Date.now()}`);
            onLog("M-3", "history.replaceState — L-11");
          }}
        >
          replaceState
        </button>
        <button
          onClick={() => {
            window.history.pushState({}, "", `${location.pathname}?p=${Date.now()}`);
            onLog("M-3", "history.pushState — L-11");
          }}
        >
          pushState
        </button>
        <button
          onClick={() => {
            location.hash = `h${Date.now()}`;
            onLog("M-3", "해시 변경 — L-11");
          }}
        >
          해시 변경
        </button>
      </div>
      <div style={{ ...row, marginTop: 8 }}>
        <button
          onClick={() => {
            onLog("M-3", "location.reload() — 문서 교체(L-09)");
            location.reload();
          }}
        >
          새로고침 (L-09)
        </button>
        <button
          onClick={() => {
            onLog("M-3", `허용 목록 밖으로 이동 시도 ${external} — 취소되어야 한다(E-05)`);
            location.href = external;
          }}
        >
          허용 목록 밖으로 이동 시도 (E-05)
        </button>
      </div>
      <div style={{ ...row, marginTop: 8 }}>
        <a href={external} target="_blank" rel="noreferrer" onClick={() => onLog("M-8", "target=_blank 링크")}>
          외부 링크(target=_blank)
        </a>
        <button
          onClick={async () => {
            if (!commands) return;
            try {
              await commands.openExternal({ url: external });
              onLog("M-8", `open_external 성공 ${external}`);
            } catch (reason) {
              onLog("M-8", `open_external 실패(E-14c): ${String(reason)}`);
            }
          }}
          disabled={!commands}
        >
          open_external (http/https)
        </button>
        <button
          onClick={async () => {
            if (!commands) return;
            try {
              await commands.openExternal({ url: "file:///etc/hosts" });
              onLog("M-8", "file:// 이 열렸다 — E-04 위반");
            } catch (reason) {
              onLog("M-8", `open_external 이 http(s) 아닌 주소를 거절했다(E-04): ${String(reason)}`);
            }
          }}
          disabled={!commands}
        >
          open_external (file:// — 거절되어야 한다)
        </button>
      </div>
    </section>
  );
}

/** M-5 — 앱을 껐다 켰을 때 웹뷰가 저장소를 들고 있는가(구조 확인). */
function CookiePanel({ onLog }: { onLog: (tag: string, text: string) => void }) {
  const [seen, setSeen] = useState<string>("");

  const readAll = useCallback(() => {
    setSeen(document.cookie || "(없음)");
    onLog("M-5", `document.cookie = ${document.cookie || "(없음)"}`);
  }, [onLog]);

  useEffect(() => {
    readAll();
  }, [readAll]);

  return (
    <section style={box}>
      <h2 style={head}>M-5 — 웹뷰 저장소 지속</h2>
      <p style={note}>
        표시용 쿠키 하나를 심고 앱을 완전히 껐다 다시 연다. 남아 있으면 <strong>저장소가 유지된다</strong>는
        구조 확인이다. 제품 세션 쿠키는 <code style={mono}>HttpOnly</code> 라 문서가 읽지 못하므로
        <strong> 최종 확인은 운영 주소에서</strong> 한다(OQ-T02).
      </p>
      <div style={row}>
        <button
          onClick={() => {
            document.cookie = `scax_probe_marker=${Date.now()}; path=/; max-age=86400; SameSite=Lax; Secure`;
            readAll();
          }}
        >
          표시용 쿠키 심기
        </button>
        <button onClick={readAll}>지금 쿠키 읽기</button>
        <span style={mono}>{seen}</span>
      </div>
    </section>
  );
}

/** M-6 — 웹의 창 닫기 되묻기가 앱 창 닫기에서 도는가. */
function CloseGuardPanel({ onLog }: { onLog: (tag: string, text: string) => void }) {
  const [armed, setArmed] = useState(false);

  useEffect(() => {
    if (!armed) return;
    // 제품 `BrowserRecordingPage.tsx:22-27` 의 가드와 같은 모양이다.
    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    onLog("M-6", "beforeunload 가드를 걸었다 — 이제 창 닫기를 눌러 본다");
    return () => window.removeEventListener("beforeunload", handler);
  }, [armed, onLog]);

  return (
    <section style={box}>
      <h2 style={head}>M-6 — 창 닫기 되묻기</h2>
      <p style={note}>
        가드를 건 뒤 <strong>창 닫기</strong>를 누른다. 확인 창이 <strong>하나만</strong> 떠야 하고(AC-T38),
        답하기 전에 창이 즉시 닫히면 안 된다(AC-T32). 셸 로그의
        <code style={mono}> close-requested</code> 줄과 함께 본다 —
        <strong>요청만으로는 아무것도 풀리지 않는다</strong>(I-4).
      </p>
      <div style={row}>
        <label>
          <input type="checkbox" checked={armed} onChange={(event) => setArmed(event.target.checked)} /> beforeunload
          가드 걸기
        </label>
      </div>
    </section>
  );
}

function LogPanel({ log, onClear }: { log: LogLine[]; onClear: () => void }) {
  return (
    <section style={box}>
      <h2 style={head}>페이지 쪽 관측 로그</h2>
      <p style={note}>
        셸 쪽 관측(네비게이션 훅 · 창 닫기 · 연결 실패 · 점유 정리)은 <strong>셸 터미널</strong>의
        <code style={mono}> [probe] </code>줄에 있다. 두 기록을 시각으로 맞춰 읽는다.
      </p>
      <div style={row}>
        <button onClick={onClear}>지우기</button>
      </div>
      <pre style={{ ...mono, marginTop: 8, maxHeight: 280, overflow: "auto" }}>
        {log.map((line) => `${line.at} [${line.tag}] ${line.text}`).join("\n")}
      </pre>
    </section>
  );
}
