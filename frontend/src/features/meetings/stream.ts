import { useCallback, useEffect, useRef, useState } from "react";

import type { MeetingAgenda, MeetingLine, MeetingTodo } from "../../lib/viewModels";
import { AUDIO_DECLARATION, startMicrophone, type MicrophoneHandle } from "./microphone";

/**
 * 회의 스트림 클라이언트 — **이 파일이 `WebSocket` 을 만드는 유일한 자리다** (`api.ts` 와 같은 규약).
 *
 * 브라우저는 STT provider 를 모른다 (SPEC §5.2) — 마이크에서 뜬 청크를 우리 서버의 회의 스트림으로만
 * 보내고, provider 주소·키·임시 credential 을 화면이 보지 않는다.
 *
 * **자동 재연결·재시도를 두지 않는다** (§5.2-7). 끊기면 그 사실을 그대로 드러낸다.
 * 멈췄다 잇는 조작(pause·resume)은 데모 범위 밖이다.
 */
export type MeetingStreamRole = "upstream" | "subscribe";

/** 첫 프레임이 선언하는 오디오 형식. 서버가 provider 설정에 그대로 옮긴다 (§5.2-3). */
export type AudioDeclaration = { format: string; sampleRate: number; channels: 1 };

export type TranscriptSegment = { speakerLabel: string; atMs: number; text: string };
export type TranscriptItem = { id: string; speakerLabel: string; atMs: number; endMs: number; content: string };

/** 연결이 닫힌 사유. 화면은 이것을 상태 줄에 그대로 낸다 — 조용히 다시 붙지 않는다. */
export type StreamClosure =
  | { kind: "ended" }
  | { kind: "unauthorized" }
  | { kind: "not_found" }
  | { kind: "stale_status" }
  | { kind: "taken" }
  | { kind: "disconnected"; reason: string };

type ServerFrame =
  | { type: "ready"; meetingStartedAt: string; latestBatchSeq: number; speakerCount: number }
  | { type: "transcript.partial"; segments: TranscriptSegment[] }
  | { type: "transcript.final"; item: TranscriptItem }
  /** 배치의 안건에는 후속 업무 후보가 함께 온다 (D46). BE 가 붙이기 전에는 그 자리가 없다 — 빈 배열로 읽는다. */
  | { type: "ai.batch"; seq: number; agendas: Array<Omit<MeetingAgenda, "todos"> & { todos?: MeetingTodo[] }> }
  | { type: "memo.line"; agendaId: string; line: MeetingLine }
  /* 아래 넷은 **「바뀐 것 하나」만** 싣는다 (백엔드 `f89de32` · 보고서 §4). 벌 통째 교체가 아니다 —
     메모는 한 줄씩 쌓이므로 통째로 갈아 끼우면 그 사이 남이 적은 줄이 사라진다.
     본문은 기존 프레임과 같은 모양이라 받는 쪽이 익힐 것이 없다. */
  | { type: "memo.line.updated"; agendaId: string; line: MeetingLine }
  | { type: "memo.line.removed"; agendaId: string; lineId: string }
  | { type: "agenda.added"; agenda: MeetingAgenda }
  | { type: "agenda.updated"; agenda: MeetingAgenda }
  | { type: "agenda.removed"; agendaId: string }
  | { type: "error"; code: string; reason: string };

export type MeetingStreamHandlers = {
  onReady: (frame: { meetingStartedAt: string; latestBatchSeq: number; speakerCount: number }) => void;
  onPartial: (segments: TranscriptSegment[]) => void;
  onFinal: (item: TranscriptItem) => void;
  onBatch: (batch: { seq: number; agendas: MeetingAgenda[] }) => void;
  /**
   * 누가 남긴 메모 한 줄, 또는 **누가 고친 한 줄.** 보는 창도 그 자리에서 같이 받는다.
   * 새 줄과 고친 줄이 한 손잡이인 이유는 받는 쪽이 하는 일이 같기 때문이다 — **id 로 upsert.**
   */
  onMemo: (memo: { agendaId: string; line: MeetingLine }) => void;
  /** 누가 지운 메모 한 줄. */
  onMemoRemoved: (removed: { agendaId: string; lineId: string }) => void;
  /** 회의 중에 선 안건, 또는 **누가 고친 안건.** 역시 id 로 upsert 한다. */
  onAgenda: (agenda: MeetingAgenda) => void;
  /** 누가 지운 안건. **그 안건에 매달렸던 줄도 함께 사라졌다** (§4.1-10). */
  onAgendaRemoved: (agendaId: string) => void;
  onClosed: (closure: StreamClosure) => void;
};

export type MeetingStreamSocket = { send: (chunk: ArrayBuffer) => void; close: () => void };

export function meetingStreamUrl(meetingId: string): string {
  // 같은 오리진이라 세션 쿠키가 핸드셰이크에 실린다 — 토큰을 주소에 붙이지 않는다 (§5.3 인증).
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/api/meetings/${meetingId}/stream`;
}

/**
 * 닫힌 코드를 사유로. **아는 코드 넷 말고는 전부 「끊김」이다** — `ready` 를 받기 전에 닫혀도 같다:
 * 붙지 못한 것도 끊긴 것이고, 화면은 「연결하는 중」에 머무르지 않고 그 사실을 드러내야 한다.
 * 서버가 말을 안 남기면 닫힌 코드를 그대로 사유로 쓴다 — 없는 문장을 지어내지 않는다.
 */
function closureOf(code: number, reason: string, lastError: string | null): StreamClosure {
  if (code === 1000) return { kind: "ended" };
  if (code === 4401) return { kind: "unauthorized" };
  if (code === 4404) return { kind: "not_found" };
  if (code === 4409) return reason === "meeting_stream_active" ? { kind: "taken" } : { kind: "stale_status" };
  return { kind: "disconnected", reason: lastError || reason || String(code) };
}

/** 한 회의의 스트림 하나를 연다. 첫 프레임으로 역할을 선언하고, `audio` 는 업스트림에만 싣는다. */
export function openMeetingStream(
  meetingId: string,
  role: MeetingStreamRole,
  audio: AudioDeclaration | null,
  handlers: MeetingStreamHandlers,
): MeetingStreamSocket {
  const socket = new WebSocket(meetingStreamUrl(meetingId));
  socket.binaryType = "arraybuffer";
  let ready = false;
  let lastError: string | null = null;
  let closed = false;

  socket.onopen = () => {
    // 연결 후 «첫 프레임». 누구인지는 이 프레임이 말하지 않는다 — 세션 쿠키가 말한다 (§5.3).
    socket.send(JSON.stringify(role === "upstream" && audio ? { type: "auth", role, audio } : { type: "auth", role }));
  };

  socket.onmessage = (event) => {
    if (typeof event.data !== "string") return;
    let frame: ServerFrame;
    try {
      frame = JSON.parse(event.data) as ServerFrame;
    } catch {
      return;
    }
    if (frame.type === "ready") {
      ready = true;
      handlers.onReady(frame);
      return;
    }
    if (frame.type === "transcript.partial") return handlers.onPartial(frame.segments);
    if (frame.type === "transcript.final") return handlers.onFinal(frame.item);
    if (frame.type === "ai.batch")
      return handlers.onBatch({
        seq: frame.seq,
        agendas: frame.agendas.map((agenda) => ({ ...agenda, todos: agenda.todos ?? [] })),
      });
    /* BE 가 이 프레임을 붙이기 전에는 오지 않는다 — 모양이 어긋나면 조용히 버린다.
       **새 것과 고친 것이 같은 손잡이로 간다**: 받는 쪽이 하는 일이 `id` 로 upsert 하나뿐이라,
       둘을 가르면 같은 코드를 두 벌 쓰게 되고 그때부터 한쪽만 고치는 실수가 난다. */
    if (frame.type === "memo.line" || frame.type === "memo.line.updated") {
      if (frame.agendaId && frame.line?.line_id) handlers.onMemo({ agendaId: frame.agendaId, line: frame.line });
      return;
    }
    if (frame.type === "memo.line.removed") {
      if (frame.agendaId && frame.lineId) handlers.onMemoRemoved({ agendaId: frame.agendaId, lineId: frame.lineId });
      return;
    }
    if (frame.type === "agenda.added" || frame.type === "agenda.updated") {
      if (frame.agenda?.agenda_id) handlers.onAgenda(frame.agenda);
      return;
    }
    if (frame.type === "agenda.removed") {
      if (frame.agendaId) handlers.onAgendaRemoved(frame.agendaId);
      return;
    }
    if (frame.type === "error") lastError = frame.reason || frame.code;
  };

  socket.onclose = (event) => {
    if (closed) return;
    closed = true;
    handlers.onClosed(closureOf(event.code, event.reason, lastError));
  };

  return {
    // `ready` 전에 보낸 오디오는 서버가 버린다 (§5.3) — 여기서 아예 보내지 않는다.
    send: (chunk) => {
      if (ready && socket.readyState === WebSocket.OPEN) socket.send(chunk);
    },
    close: () => {
      closed = true;
      socket.close();
    },
  };
}

export type MeetingStreamState = {
  phase: "idle" | "connecting" | "live" | "closed";
  closure: StreamClosure | null;
  startedAt: string | null;
  finals: TranscriptItem[];
  /** 잠정 발화는 «교체» 다 — 올 때마다 이전 것을 통째로 바꾸고 저장하지 않는다 (§5.3). */
  partial: TranscriptSegment[];
  /** AI 트랙은 배치가 낸 «전체» 다. 화면은 통째로 갈아 끼우고 줄 id 를 붙들지 않는다 (§7.1). */
  batch: { seq: number; agendas: MeetingAgenda[] } | null;
  /**
   * 회의가 도는 동안 도착한 메모 줄. 보는 창도 같은 프레임으로 받는다.
   * **`line_id` 로 갈아 끼운다(upsert)** — 그래서 같은 프레임이 두 번 와도, 자기가 보낸 것이
   * 방 전체 브로드캐스트로 되돌아와도 **두 번 그리지 않는다** (§3 에코 처리).
   */
  memos: Array<{ agendaId: string; line: MeetingLine }>;
  /** 지워진 메모 줄의 id. 상세가 실어 온 줄에도 걸어야 하므로 «지웠다는 사실» 을 따로 든다. */
  removedLines: string[];
  /** 회의가 도는 동안 선 안건, 그리고 고쳐진 안건. 역시 `agenda_id` 로 갈아 끼운다. */
  agendas: MeetingAgenda[];
  /** 지워진 안건의 id. 본문 목록과 **메모 대상 드롭다운**이 함께 이것을 걸러야 한다. */
  removedAgendas: string[];
  /** 마이크를 못 얻었다. 화면은 멈추지 않고 상태 줄로만 알린다. */
  micDenied: boolean;
  /**
   * 업스트림 자리를 이미 다른 창이 갖고 있어 **구독으로 붙었다.**
   * 오디오도 메모도 이 창의 것이 아니지만, 갱신되는 것은 그대로 받는다.
   */
  takenOver: boolean;
};

const IDLE: MeetingStreamState = {
  phase: "idle",
  closure: null,
  startedAt: null,
  finals: [],
  partial: [],
  batch: null,
  memos: [],
  removedLines: [],
  agendas: [],
  removedAgendas: [],
  micDenied: false,
  takenOver: false,
};

/**
 * 「진행 중」 화면이 붙는 자리. `role` 이 `upstream` 이면 `ready` 뒤에 마이크를 열어 청크를 올린다.
 * 오디오를 올리는 연결은 회의당 하나이고, 그 자리는 회의를 시작한 사람이 갖는다 (§5.2-5).
 */
export function useMeetingStream({
  meetingId,
  role,
  enabled,
  onClosed,
}: {
  meetingId: string;
  role: MeetingStreamRole;
  enabled: boolean;
  /** 닫힌 사유를 바깥이 처리한다 — 종료면 상세를 다시 읽고, 인증이면 로그인으로 보낸다. */
  onClosed?: (closure: StreamClosure) => void;
}): MeetingStreamState {
  const [state, setState] = useState<MeetingStreamState>(IDLE);
  /* 업스트림 자리를 못 얻었다 — **역할만 바꿔** 구독으로 붙는다.
     같은 자리를 다시 두드리는 재시도가 아니다: 서버가 「그 자리는 찼다」고 말한 대로 다른 자리로 간다. */
  const [takenOver, setTakenOver] = useState(false);
  const closedRef = useRef(onClosed);
  closedRef.current = onClosed;
  const effectiveRole: MeetingStreamRole = role === "upstream" && takenOver ? "subscribe" : role;

  useEffect(() => {
    if (!enabled) {
      setState(IDLE);
      setTakenOver(false);
      return;
    }
    let stopped = false;
    let microphone: MicrophoneHandle | null = null;
    setState({ ...IDLE, phase: "connecting" });

    const socket = openMeetingStream(meetingId, effectiveRole, effectiveRole === "upstream" ? AUDIO_DECLARATION : null, {
      onReady: (frame) => {
        if (stopped) return;
        setState((current) => ({ ...current, phase: "live", startedAt: frame.meetingStartedAt }));
        if (effectiveRole !== "upstream") return;
        void startMicrophone((chunk) => socket.send(chunk))
          .then((handle) => {
            if (stopped) handle.stop();
            else microphone = handle;
          })
          .catch(() => {
            // 마이크를 거부해도 화면은 멈추지 않는다 — 스크립트와 AI 요약은 계속 받는다.
            if (!stopped) setState((current) => ({ ...current, micDenied: true }));
          });
      },
      onPartial: (segments) => {
        if (!stopped) setState((current) => ({ ...current, partial: segments }));
      },
      onFinal: (item) => {
        if (!stopped)
          setState((current) => ({ ...current, finals: [...current.finals, item], partial: [] }));
      },
      onBatch: (batch) => {
        if (!stopped) setState((current) => ({ ...current, batch }));
      },
      /* ── 아래 넷이 **멱등** 이다 ──
         전부 «id 로 제자리를 짚어» 갈아 끼우거나 지운다. 그래서 같은 프레임을 두 번 받아도,
         자기가 보낸 것이 되돌아와도, 한 번 그린 것과 결과가 같다. 붙이기(`push`)가 아니라
         갈아 끼우기(`upsert`)인 것이 핵심이다 — 예전 `memo.line` 은 붙이기였고, 받는 쪽
         (`MeetingDetailPage`)이 뒤에서 한 번 더 걸러 주고 있어서 겨우 두 번 안 그렸다. */
      onMemo: (memo) => {
        if (stopped) return;
        setState((current) => {
          const at = current.memos.findIndex((one) => one.line.line_id === memo.line.line_id);
          if (at < 0) return { ...current, memos: [...current.memos, memo] };
          const memos = [...current.memos];
          memos[at] = memo;
          return { ...current, memos };
        });
      },
      onMemoRemoved: ({ lineId }) => {
        if (stopped) return;
        setState((current) => ({
          ...current,
          memos: current.memos.filter((one) => one.line.line_id !== lineId),
          // 상세가 실어 온 줄은 여기 없다 — 지웠다는 «사실» 을 남겨 그쪽도 걸러지게 한다
          removedLines: current.removedLines.includes(lineId) ? current.removedLines : [...current.removedLines, lineId],
        }));
      },
      onAgenda: (agenda) => {
        if (stopped) return;
        setState((current) => {
          const at = current.agendas.findIndex((one) => one.agenda_id === agenda.agenda_id);
          if (at < 0) return { ...current, agendas: [...current.agendas, agenda] };
          const agendas = [...current.agendas];
          agendas[at] = agenda;
          return { ...current, agendas };
        });
      },
      onAgendaRemoved: (agendaId) => {
        if (stopped) return;
        setState((current) => ({
          ...current,
          agendas: current.agendas.filter((one) => one.agenda_id !== agendaId),
          // 안건이 지워지면 **그 벌의 줄도 함께 사라졌다** (§4.1-10) — 들고 있던 줄을 같이 놓는다
          memos: current.memos.filter((one) => one.agendaId !== agendaId),
          removedAgendas: current.removedAgendas.includes(agendaId)
            ? current.removedAgendas
            : [...current.removedAgendas, agendaId],
        }));
      },
      onClosed: (closure) => {
        microphone?.stop();
        microphone = null;
        if (!stopped) setState((current) => ({ ...current, phase: "closed", closure, partial: [] }));
        // 오디오를 올릴 자리는 회의당 하나다 — 그 자리가 찼으면 구독으로 붙어 갱신은 그대로 받는다
        if (closure.kind === "taken" && effectiveRole === "upstream") setTakenOver(true);
        closedRef.current?.(closure);
      },
    });

    return () => {
      stopped = true;
      microphone?.stop();
      socket.close();
    };
  }, [effectiveRole, enabled, meetingId]);

  return { ...state, takenOver };
}
