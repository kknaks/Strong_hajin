import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import {
  ApiError,
  addMeetingAgenda,
  endMeeting,
  getWorkRequestAssigneeCandidates,
  getWorkRequestCcCandidates,
  detachMeetingMaterial,
  readMeeting,
  readMeetingMaterials,
  readMeetingTranscript,
  meetingExportUrl,
  promoteMeetingTodo,
  removeMeetingAgenda,
  removeMeetingTodo,
  retryMeetingFinalize,
  startMeeting,
  updateMeetingAgenda,
  updateMeetingInfo,
} from "../api";
import { Empty } from "../Empty";
import { FileList } from "../FileList";
import { Icon } from "../Icon";
import { Drawer } from "../Modal";
import { Skeleton } from "../Skeleton";
import { StatusNote } from "../StatusNote";
import {
  meetingAgendaSourceText,
  meetingBadgeClass,
  meetingClock,
  meetingDateInput,
  meetingIsoAt,
  meetingRange,
  meetingScreen,
  meetingSpeakerName,
  meetingElapsed,
  meetingStatusLabel,
  meetingTimeOptions,
  personName,
} from "../labels";
import type {
  MeetingAgenda,
  MeetingEvidence,
  MeetingLine,
  MeetingMaterial,
  MeetingRecord,
  MeetingTodo,
  MeetingTranscript,
  Persona,
} from "../viewModels";
import { AgendaBlock, type AgendaLineView } from "./AgendaBlock";
import { AttachModal, failureText, sizeText } from "./AttachModal";
import { MaterialDrawer } from "./MaterialDrawer";
import { LiveScript, type ScriptRow } from "./LiveScript";
import { MemoComposer } from "./MemoComposer";
import { useMeetingStream, type StreamClosure } from "./stream";
import { BookingModal, roomReservationNotice } from "./BookingModal";
import { PersonSearch, PickedTags } from "./PeoplePicker";
import { ShareModal } from "./ShareModal";
import { CreateWorkDrawer } from "../WorkModals";
import { useRoster, type RosterPerson } from "./roster";

type Attendee = { member_id: string; display_name: string };

/**
 * 「정리 중」 동안 상태를 다시 묻는 간격.
 *
 * 합성은 자동이라 사람이 눌러 넘기는 단계가 없고(§8-2·§8-6), 끝났다는 것을 알려 줄 스트림도 그때는
 * 이미 닫혀 있다 — 다시 묻지 않으면 화면이 스켈레톤에 갇힌다.
 */
const SETTLING_POLL_MS = 5_000;


/** 409 저장 충돌이면 서버가 함께 낸 「지금 있는 안건」을 꺼낸다. 그 밖의 오류면 `null`. */
function staleAgendaOf(reason: unknown): MeetingAgenda | null {
  if (!(reason instanceof ApiError) || reason.status !== 409) return null;
  const detail = reason.detail as { code?: string; current?: MeetingAgenda } | undefined;
  return detail?.code === "meeting_agenda_stale" && detail.current ? detail.current : null;
}

function linesOf(agenda: MeetingAgenda, track: "memo" | "ai" | "final"): string[] {
  return agenda.lines
    .filter((line) => line.track === track)
    .sort((left, right) => left.order - right.order)
    .map((line) => line.text);
}

/**
 * SCR-106 회의 상세 — 상태 여섯이 **한 화면**에서 갈린다 (X-93).
 *
 * 무엇이 서는지는 `status` · `viewer_relation` · `can_edit_info` · `can_edit_note` 넷이 정한다.
 * 역할(kind)로 추론하지 않는다 — 서버가 못 준 권한을 화면이 지어내지 않는다.
 *
 * 「진행 중」은 회의 스트림에 붙어 산다 — 실시간 원문 · AI 중간 요약 · 메모. 자동 재연결을 두지 않는다:
 * 끊기면 상태 줄이 그 사실을 그대로 낸다 (SPEC §5.2-7).
 */
export function MeetingDetailPage({
  meetingId,
  onBack,
  onOpenMeeting,
  onSessionLost,
  ownerName,
  canCreateWorkRequests,
  onError,
  onNotice,
  onTitleChange,
  onRegisterLeaveGuard,
  onRegisterRefresh,
}: {
  meetingId: string;
  onBack: () => void;
  onOpenMeeting: (meetingId: string) => void;
  /** 스트림이 인증으로 닫혔다 — 세션이 죽었으므로 로그인으로 돌려보낸다. */
  onSessionLost: () => void;
  ownerName: string;
  canCreateWorkRequests: boolean;
  onError: (message: string | null) => void;
  onNotice: (message: string) => void;
  onTitleChange?: (title: string) => void;
  /** 고치던 것이 있으면 나가기 전에 한 번 묻는다 (T11). */
  onRegisterLeaveGuard?: (guard: ((proceed: () => void) => void) | null) => void;
  onRegisterRefresh?: (refresh: (() => Promise<void>) | null) => void;
}) {
  const [record, setRecord] = useState<MeetingRecord | null>(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [left, setLeft] = useState<"memo" | "ai">("memo");
  const [right, setRight] = useState<"materials" | "script">("materials");
  /** 근거 칩이 가리키는 구간. 스크립트의 그 자리가 켜지고 눈에 들어온다 (I05). */
  const [marked, setMarked] = useState<{ startMs: number; endMs: number } | null>(null);
  const [transcript, setTranscript] = useState<MeetingTranscript | null>(null);

  const [editing, setEditing] = useState(false);
  const [bodies, setBodies] = useState<Record<string, string[]>>({});
  const [agendaDraft, setAgendaDraft] = useState("");
  const [askRemoveAgenda, setAskRemoveAgenda] = useState<MeetingAgenda | null>(null);

  const [headEdit, setHeadEdit] = useState(false);
  const [head, setHead] = useState<{ title: string; date: string; from: string; to: string; place: string; people: Attendee[] } | null>(null);
  const [headQuery, setHeadQuery] = useState("");

  const [share, setShare] = useState(false);
  const [attach, setAttach] = useState(false);
  const [materials, setMaterials] = useState<MeetingMaterial[] | null>(null);
  const [openMaterial, setOpenMaterial] = useState<string | null>(null);
  const [askLeave, setAskLeave] = useState<(() => void) | null>(null);
  const [askDropMaterial, setAskDropMaterial] = useState<MeetingMaterial | null>(null);
  // [업무 생성] — 후보 한 줄을 업무 요청 모달에 옮겨 담는다. 담당만 비워 두고 사람이 고른다.
  const [promoting, setPromoting] = useState<MeetingTodo | null>(null);
  const [assigneeCandidates, setAssigneeCandidates] = useState<Persona[]>([]);
  const [ccCandidates, setCcCandidates] = useState<Persona[]>([]);
  const [booking, setBooking] = useState(false);
  /** 이 세션에서 던진 메모 — 응답으로 돌아온 줄만 담는다 (낙관 렌더 없음). */
  /** 이 세션에서 던진 메모 — 응답으로 돌아온 줄만 담는다 (낙관 렌더 없음). 시각도 서버가 매긴 것을 쓴다. */
  const [sessionMemos, setSessionMemos] = useState<Array<{ agendaId: string; line: MeetingLine }>>([]);
  const noteScroll = useRef<HTMLDivElement | null>(null);
  const keepScroll = useRef<{ top: number; pinned: boolean } | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const { roster } = useRoster(headEdit);

  // 요청을 받을 수 있는 사람은 서버가 정한다 — 화면이 명부에서 고르지 않는다.
  useEffect(() => {
    if (!promoting) return;
    let cancelled = false;
    void Promise.all([getWorkRequestAssigneeCandidates(), getWorkRequestCcCandidates().catch(() => [] as Persona[])])
      .then(([assignees, cc]) => {
        if (cancelled) return;
        setAssigneeCandidates(assignees);
        setCcCandidates(cc);
      })
      .catch(() => {
        if (!cancelled) setAssigneeCandidates([]);
      });
    return () => {
      cancelled = true;
    };
  }, [promoting]);

  const reload = useCallback(async () => {
    const next = await readMeeting(meetingId);
    setRecord(next);
  }, [meetingId]);

  useEffect(() => {
    let cancelled = false;
    setRecord(null);
    setFailed(false);
    void reload().catch(() => {
      if (!cancelled) setFailed(true);
    });
    return () => {
      cancelled = true;
    };
  }, [reload]);

  useEffect(() => {
    onRegisterRefresh?.(reload);
    return () => onRegisterRefresh?.(null);
  }, [onRegisterRefresh, reload]);

  /* 끝난 회의의 원문은 서버가 갖고 있다 — 「진행 중」만 스트림에서 오고 나머지는 이 자리를 읽는다.
     예정·취소됨에는 탭 자체가 서지 않으므로 읽지 않는다 (§5.4-7). */
  /* 「진행 중」에도 읽는다 — 중간에 들어온 사람은 이미 적재된 확정 블록부터 봐야 한다.
     주최자 창에서는 그 뒤로 스트림의 `transcript.final` 이 이어 붙고, 스트림이 없는 창은 폴링이 따라간다. */
  /* 상태가 바뀌면 그때마다 다시 읽는다 — 「참」이 「참」으로 남는 조건이면 한 번 읽고 마는데,
     끝난 회의의 원문은 서버가 재전사로 통째 갈아 끼우므로(in_progress 때 본 것과 다른 글이다)
     in_progress→summarizing→done 마디마다 새로 읽어야 그 글이 화면에 온다 (D2). */
  const transcriptStatus = ["scheduled", "cancelled"].includes(record?.meeting.status ?? "")
    ? null
    : record?.meeting.status ?? null;
  const loadTranscript = useCallback(async () => {
    setTranscript(await readMeetingTranscript(meetingId));
  }, [meetingId]);
  useEffect(() => {
    if (!transcriptStatus) return;
    let cancelledRead = false;
    void loadTranscript().catch(() => {
      if (!cancelledRead) setTranscript({ items: [], memos: [] });
    });
    return () => {
      cancelledRead = true;
    };
  }, [loadTranscript, transcriptStatus]);

  /* 자료는 참석자와 공유받은 사람이 함께 읽는다 — 탭이 서는 사람만 부른다 */
  const loadMaterials = useCallback(async () => {
    setMaterials(await readMeetingMaterials(meetingId));
  }, [meetingId]);
  useEffect(() => {
    if (!record) return;
    let cancelledRead = false;
    void loadMaterials().catch(() => {
      if (!cancelledRead) setMaterials([]);
    });
    return () => {
      cancelledRead = true;
    };
  }, [loadMaterials, record]);

  const meeting = record?.meeting ?? null;
  useEffect(() => {
    if (meeting) onTitleChange?.(meeting.title ?? meetingScreen.noTitle);
  }, [meeting, onTitleChange]);

  const dirty = editing || headEdit;
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  useEffect(() => {
    onRegisterLeaveGuard?.((proceed) => {
      if (dirtyRef.current) setAskLeave(() => proceed);
      else proceed();
    });
    return () => onRegisterLeaveGuard?.(null);
  }, [onRegisterLeaveGuard]);

  /* `order` 로 줄만 세운다 — 번호는 그 줄에서의 «자리» 다 (바로 시작한 회의는 order 0 에서 시작한다).
     한 자리에서 세워 두면 블록·메모 드롭다운·저장이 같은 줄을 본다. */
  const status = meeting?.status;
  const planned = status === "scheduled";
  const live = status === "in_progress";
  const settling = status === "summarizing";
  const settled = status === "done";
  const failedState = status === "failed";
  const cancelled = status === "cancelled";
  const attendee = meeting?.viewer_relation === "attendee";

  /* 고치는 권한은 둘로 갈라져 있다 — 서버가 각각 말한다. 상태로 추론하지 않는다.
     `can_edit_agendas` 안건을 더하고 뺀다 (예정 · 완료 · 실패 · 취소됨)
     `can_edit_note`    회의록 줄을 고친다 (완료 · 실패 · 취소됨)
     [수정]은 그 둘 중 하나라도 열려 있으면 서는 한 자리다 (E77) — 「예정」은 회의록이 비어 있어도
     안건은 손봐야 하므로, 줄 편집만 보고 버튼을 감추면 안건을 더할 자리가 사라진다. */
  const canEditNote = Boolean(meeting?.can_edit_note) && !live && !settling;
  const canEditAgendas = Boolean(meeting?.can_edit_agendas) && !live && !settling;
  const canEdit = canEditNote || canEditAgendas;
  const noteEditing = editing && canEdit;
  const agendaEditing = noteEditing && canEditAgendas;
  /* 「취소됨」은 줄을 고칠 수 있어도 회의록이 비어 있다 — 화면은 「예정」과 같은 안건 목록이다 (§5.7) */
  const lineEditing = noteEditing && canEditNote && (settled || failedState);

  /* 오디오를 올리는 연결은 회의당 하나이고 그 자리는 회의를 시작한 사람이 갖는다 (§5.2-5).
     누가 그 사람인지는 **서버가 말한다** — 화면이 `created_by` 로 추론하지 않는다.
     **회의 스트림 하나에 모두가 붙는다** — 주최자는 올리고, 그 밖의 참석자는 구독으로 받기만 한다. */
  const canWriteMemo = Boolean(meeting?.can_write_memo);
  const onStreamClosed = useCallback(
    (closure: StreamClosure) => {
      // 종료(1000)와 상태 불일치(4409)는 화면이 낡았다는 뜻이다 — 상세를 다시 읽는다.
      if (closure.kind === "ended" || closure.kind === "stale_status") void reload().catch(() => setFailed(true));
      if (closure.kind === "unauthorized") onSessionLost();
    },
    [onSessionLost, reload],
  );
  const stream = useMeetingStream({
    meetingId,
    role: canWriteMemo ? "upstream" : "subscribe",
    enabled: live && attendee,
    onClosed: onStreamClosed,
  });
  /* 회의 중에 선 안건은 스트림으로도 온다 — 상세를 다시 읽기 전에도 목록에 세운다. 같은 안건은 한 번만 */
  const agendas = [
    ...new Map([...(record?.agendas ?? []), ...stream.agendas].map((agenda) => [agenda.agenda_id, agenda])).values(),
  ].sort((left, right) => left.order - right.order);

  /* 업스트림 자리를 이미 다른 창이 갖고 있다 — 이 창은 구독으로 붙어 읽기만 한다 */
  const takenElsewhere = stream.takenOver;
  /** 이 창이 회의를 「이끄는」 창인가 — 마이크·메모·[회의 종료]가 여기에만 선다. */
  const hosting = live && canWriteMemo && !takenElsewhere;

  // 경과 시간은 회의 시작 시각에서 흐른다 — `ready` 가 준 값이 정본이고, 오기 전에는 예정 시각으로 센다.
  useEffect(() => {
    if (!live) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [live]);

  /* 「정리 중」에는 상태가 서버에서 혼자 넘어간다 — 다 됐다고 알려 줄 연결이 그때는 이미 닫혀 있다.
     완료·실패·취소됨이 되면 `settling` 이 거짓이 되어 이 자리가 스스로 걷힌다. */
  useEffect(() => {
    if (!settling) return;
    const timer = window.setInterval(() => {
      void reload().catch(() => {
        /* 한 번 못 읽었다고 묻기를 그만두지 않는다 — 다음 차례에 다시 묻는다. */
      });
    }, SETTLING_POLL_MS);
    return () => window.clearInterval(timer);
  }, [reload, settling]);

  /* AI 배치는 트랙을 통째로 갈아 끼운다 — 읽던 자리가 튀지 않게 스크롤을 그대로 둔다.
     바닥에 붙어 보고 있었으면 바닥에 붙여 둔다. */
  useEffect(() => {
    const node = noteScroll.current;
    if (!node) return;
    keepScroll.current = {
      top: node.scrollTop,
      pinned: node.scrollHeight - node.scrollTop - node.clientHeight < 8,
    };
  }, [stream.batch?.seq]);
  useLayoutEffect(() => {
    const node = noteScroll.current;
    const kept = keepScroll.current;
    if (!node || !kept) return;
    node.scrollTop = kept.pinned ? node.scrollHeight : kept.top;
  }, [stream.batch]);

  const leftTabs = live && attendee;
  const memoTab = leftTabs && left === "memo";
  const aiTab = live && (!leftTabs || left === "ai");
  const tabMaterials = attendee;
  const tabScript = !planned && !cancelled;
  const rightShown = tabMaterials || tabScript;
  const materialsOn = tabMaterials && (right === "materials" || !tabScript);
  const scriptOn = tabScript && !materialsOn;

  const draftOf = useCallback(
    (agenda: MeetingAgenda) => bodies[agenda.agenda_id] ?? linesOf(agenda, "final"),
    [bodies],
  );

  const headUnchanged = useMemo(() => {
    if (!head || !meeting) return true;
    return (
      head.title === (meeting.title ?? "") &&
      head.date === meetingDateInput(meeting.starts_at) &&
      head.from === meetingClock(meeting.starts_at) &&
      head.to === meetingClock(meeting.ends_at) &&
      head.place === (meeting.location ?? "") &&
      head.people.map((one) => one.member_id).join() === meeting.attendees.map((one) => one.member_id).join()
    );
  }, [head, meeting]);

  if (failed) {
    return (
      <section className="page-surface meeting-surface">
        <div className="meeting-panel-center">
          <Empty onAction={() => void reload().catch(() => setFailed(true))} title={meetingScreen.listError} variant="error" />
        </div>
      </section>
    );
  }
  if (!record || !meeting) {
    return (
      <section className="page-surface meeting-surface">
        <div style={{ padding: 24 }}>
          <Skeleton label="회의를 불러오는 중" />
        </div>
      </section>
    );
  }

  async function run(work: () => Promise<unknown>, notice?: string) {
    if (busy) return;
    setBusy(true);
    try {
      await work();
      await reload();
      onError(null);
      if (notice) onNotice(notice);
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "처리하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  function openHeadEdit() {
    if (!meeting) return;
    setHead({
      // 제목이 비어 있으면 후보를 칸에 채워 연다 — 사람이 그대로 저장하면 그것이 제목이 된다
      title: meeting.title ?? meeting.title_candidate ?? "",
      date: meetingDateInput(meeting.starts_at),
      from: meetingClock(meeting.starts_at),
      to: meetingClock(meeting.ends_at),
      place: meeting.location ?? "",
      people: meeting.attendees.map((one) => ({ ...one })),
    });
    setHeadQuery("");
    setHeadEdit(true);
  }

  async function saveHead() {
    if (!head || !meeting) return;
    await run(
      () =>
        updateMeetingInfo(meeting.meeting_id, {
          title: head.title.trim() || null,
          starts_at: meetingIsoAt(head.date, head.from),
          ends_at: meetingIsoAt(head.date, head.to),
          location: head.place.trim() || null,
          attendee_ids: head.people.map((one) => one.member_id),
        }),
      meetingScreen.saved,
    );
    setHeadEdit(false);
    setHeadQuery("");
  }

  /** [저장] — 줄 편집은 덮어쓰기다. 빈 줄은 보내기 전에 버린다. */
  async function saveNote() {
    if (!meeting) return;
    // 안건만 고치는 상태(예정 · 취소됨)에서는 [저장]이 편집을 닫기만 한다 — 안건은 누를 때 이미 갔다.
    if (!lineEditing) {
      setEditing(false);
      return;
    }
    const meetingKey = meeting.meeting_id;
    const changed = agendas.filter((agenda) => bodies[agenda.agenda_id] !== undefined);
    if (busy) return;
    setBusy(true);
    try {
      for (const agenda of changed) {
        try {
          // 줄별로 보내지 않는다 — 안건 하나를 통째로 덮어쓰고, 읽은 시각을 함께 보낸다 (§8-9).
          await updateMeetingAgenda(meetingKey, agenda.agenda_id, {
            lines: draftOf(agenda).map((text) => text.trim()).filter(Boolean),
            expected_last_saved_at: agenda.last_saved_at,
          });
        } catch (reason) {
          const stale = staleAgendaOf(reason);
          if (!stale) throw reason;
          /* 덮어쓰지 않는다 — 서버가 함께 준 「지금 있는 것」으로 갈아 끼우고 사람에게 말한다 (§8-9) */
          setBodies((current) => ({
            ...current,
            [agenda.agenda_id]: stale.lines.filter((line) => line.track === "final").map((line) => line.text),
          }));
          onNotice(meetingScreen.savedElsewhere);
          await reload();
          return;
        }
      }
      await reload();
      onError(null);
      onNotice(meetingScreen.saved);
      setBodies({});
      setEditing(false);
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "저장하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  /* 회의 시작 시각은 `ready` 가 준 값이 정본이다 — 오기 전에는 예정 시각으로 센다 */
  const startedAtMs = new Date(stream.startedAt ?? meeting.started_at ?? meeting.starts_at).getTime();
  const elapsed = meetingElapsed(now - startedAtMs);

  /* 상태 줄 — 연결 중 / 진행 중 / 끊김(사유) / 다른 창에서 진행 중. 조용히 다시 붙지 않는다 */
  const streamNotice = (() => {
    if (!live || !attendee) return null;
    // 업스트림 자리를 못 얻어 구독으로 붙었다 — 붙어 있어도 그 사실은 계속 말한다
    if (takenElsewhere) return meetingScreen.streamTaken;
    if (stream.phase === "connecting") return meetingScreen.streamConnecting;
    if (stream.phase !== "closed" || !stream.closure) return null;
    const closure = stream.closure;
    if (closure.kind === "taken") return meetingScreen.streamTaken;
    if (closure.kind === "not_found") return meetingScreen.streamNotFound;
    if (closure.kind === "unauthorized") return meetingScreen.streamUnauthorized;
    if (closure.kind === "ended" || closure.kind === "stale_status") return null;
    return closure.reason ? `${meetingScreen.streamDisconnected} ${closure.reason}` : meetingScreen.streamDisconnected;
  })();

  /* AI 배치가 왔으면 그 회차가 낸 트랙 «전체» 를 쓴다 — 줄 id 를 붙들지 않고 통째로 갈아 끼운다 (§7.1) */
  const shownAgendas =
    aiTab && stream.batch ? [...stream.batch.agendas].sort((left, right) => left.order - right.order) : agendas;

  /**
   * 줄 하나를 보는 모양으로 — 딛는 구간을 «전부» 시각 칩으로 싣는다 (D49).
   * 하나만 내던 때(D34)는 두 번째·세 번째 근거에 닿을 길이 자체가 없었다.
   * **회의 중에도 누를 수 있다** (D3): AI 요약 탭의 줄도 스크립트의 그 자리로 간다.
   */
  const lineViews = (lines: MeetingLine[]): AgendaLineView[] => {
    const jumpable = !noteEditing && attendee;
    return [...lines]
      .sort((left, right) => left.order - right.order)
      .map((line) => {
        /* 시작이 같은 구간은 하나로 접는다 — 같은 시각 칩이 둘 서면 어느 쪽인지 가릴 수가 없다.
           접을 때는 넓은 쪽(끝이 늦은 쪽)을 남겨 그 자리가 다 켜지게 한다. */
        const spans = new Map<number, MeetingEvidence>();
        for (const span of line.evidence) {
          if (!Number.isFinite(span.start_ms) || !Number.isFinite(span.end_ms)) continue;
          const kept = spans.get(span.start_ms);
          if (!kept || span.end_ms > kept.end_ms) spans.set(span.start_ms, span);
        }
        const chips = [...spans.values()]
          .sort((left, right) => left.start_ms - right.start_ms)
          .map((span) => ({
            label: meetingElapsed(span.start_ms),
            onJump: jumpable
              ? () => {
                  setMarked({ startMs: span.start_ms, endMs: span.end_ms });
                  setRight("script");
                }
              : undefined,
          }))
          // 시작 시각을 못 읽는 구간은 칩을 세우지 않는다 — 없는 시각을 지어내지 않는다
          .filter((chip) => chip.label !== "");
        return { text: line.text, chips: chips.length > 0 ? chips : undefined };
      });
  };

  const tracked = (agenda: MeetingAgenda, track: "memo" | "ai" | "final") =>
    lineViews(agenda.lines.filter((line) => line.track === track));

  /** 이 세션에서 오간 메모 — 내가 던져 돌아온 줄과 스트림으로 온 줄. 같은 줄은 한 번만 선다. */
  const liveMemos = [...new Map([...sessionMemos, ...stream.memos].map((memo) => [memo.line.line_id, memo])).values()];
  const memosOf = (agendaId: string) =>
    liveMemos.filter((memo) => memo.agendaId === agendaId).map((memo) => memo.line.text);

  /**
   * 이 줄이 칩이 가리킨 구간과 «겹치는가» (D50).
   *
   * 줄의 시작 시각만 구간 안에 있는지 보던 때는, 구간이 발화 한가운데서 시작하면(AI 가 문장 일부를 근거로
   * 잡으면 흔한 일이다) 아무 줄도 켜지지 않았다 — 실측 여덟 구간 중 다섯이 그랬다. 겹치기만 하면 켠다.
   */
  const overlapsMarked = (row: { atMs: number; endMs: number }) =>
    marked !== null && row.atMs <= marked.endMs && row.endMs >= marked.startMs;

  /* 「스크립트」 탭 — 「진행 중」은 스트림이 밀어 준 것을, 끝난 회의는 서버가 가진 원문을 그린다.
     **전사만 선다** (사용자 결정) — 메모는 왼쪽 「메모」 탭의 것이고 같은 말을 두 자리에 두지 않는다.
     확정은 쌓이고 잠정은 맨 끝에 회색으로 한 벌 선다 */
  const scriptRows: ScriptRow[] = [
    ...new Map(
      [...(transcript?.items ?? []), ...(live ? stream.finals : [])].map((item) => [
        item.id,
        {
          key: item.id,
          kind: "talk" as const,
          who: meetingSpeakerName(item.speakerLabel),
          atMs: item.atMs,
          endMs: item.endMs,
          text: item.content,
        },
      ]),
    ).values(),
  ]
    .sort((left, right) => left.atMs - right.atMs)
    .map((row) => ({ ...row, active: overlapsMarked(row) }));
  for (const [index, segment] of stream.partial.entries()) {
    scriptRows.push({
      key: `partial-${index}`,
      kind: "talk",
      who: meetingSpeakerName(segment.speakerLabel),
      atMs: segment.atMs,
      // 잠정 줄은 아직 끝을 모른다 — 시작과 같은 자리로 둔다
      endMs: segment.atMs,
      text: segment.text,
      tentative: true,
    });
  }

  const badgeClass = status ? meetingBadgeClass[status] : undefined;
  const facts = [meetingRange(meeting.starts_at, meeting.ends_at), meeting.location, meetingScreen.attendCount(meeting.attendees.length + meeting.external_attendees.length)]
    .filter(Boolean)
    .join(" · ");
  const headPeopleIds = new Set((head?.people ?? []).map((one) => one.member_id));
  /* 담당 후보는 회의 참석자를 앞에 세운다 — 그 자리에 있던 사람이 먼저 걸린다. 목록 자체는 서버가 준 것 그대로다 */
  const attendeeIds = new Set(meeting.attendees.map((one) => one.member_id));
  const orderedAssignees = [
    ...assigneeCandidates.filter((one) => attendeeIds.has(one.id)),
    ...assigneeCandidates.filter((one) => !attendeeIds.has(one.id)),
  ];

  return (
    <section className="page-surface meeting-surface">
      {/* E12 진행 표시 — 회의가 도는 동안 위에 남는다 */}
      {live && (
        <div className="meeting-live-bar">
          <span style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <span className="status in_progress" style={{ fontSize: 13 }}>
              {meetingStatusLabel.in_progress}
            </span>
            <span className="tabular" style={{ fontSize: 14, fontWeight: 700, color: "var(--action)" }}>
              {elapsed}
            </span>
            {streamNotice && (
              <StatusNote tone={stream.phase === "closed" && !takenElsewhere ? "danger" : "muted"}>{streamNotice}</StatusNote>
            )}
            {stream.micDenied && <StatusNote tone="danger">{meetingScreen.micDenied}</StatusNote>}
          </span>
          <span className="t-meta" style={{ fontSize: 12 }}>
            {meetingScreen.startedBy(personName(meeting.created_by))}
          </span>
        </div>
      )}

      <div className={headEdit ? "page-head meeting-head editing" : "page-head meeting-head"}>
        <div style={{ minWidth: 0, flex: 1 }}>
          {/* E76 구획 제목 · 그 옆이 예약값을 고치는 자리다 */}
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
            <span className="t-meta" style={{ fontSize: 12 }}>
              {meetingScreen.info}
            </span>
            {headEdit ? (
              <>
                <button className="btn ghost h30 meeting-head-btn" onClick={() => setHeadEdit(false)} type="button">
                  {meetingScreen.cancel}
                </button>
                <button className="btn h30 meeting-head-btn" disabled={headUnchanged || busy} onClick={() => void saveHead()} type="button">
                  {meetingScreen.save}
                </button>
              </>
            ) : (
              /* 연필 — 예약값을 고치는 자리. 「예정」·「완료」에만 서고, 열 수 있는지는 서버가 말한다 */
              meeting.can_edit_info &&
              (planned || settled) && (
                <button
                  aria-label={meetingScreen.editInfo}
                  className="btn ghost icon h30 meeting-head-btn"
                  onClick={openHeadEdit}
                  type="button"
                >
                  <PencilGlyph />
                </button>
              )
            )}
          </div>

          {headEdit && head ? (
            <div className="meeting-meta-edit">
              <div style={{ display: "flex", flexDirection: "column", gap: 12, minWidth: 0 }}>
                <div className="field">
                  <label htmlFor="meeting-head-title">{meetingScreen.titleField}</label>
                  <input
                    id="meeting-head-title"
                    onChange={(event) => setHead({ ...head, title: event.target.value })}
                    placeholder={meetingScreen.subjectPlaceholder}
                    type="text"
                    value={head.title}
                  />
                </div>
                <div className="field">
                  <label htmlFor="meeting-head-date">{meetingScreen.whenField}</label>
                  <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    <input
                      id="meeting-head-date"
                      onChange={(event) => setHead({ ...head, date: event.target.value })}
                      style={{ width: "auto", flex: 1, minWidth: 0 }}
                      type="date"
                      value={head.date}
                    />
                    <select aria-label="시작 시각" onChange={(event) => setHead({ ...head, from: event.target.value })} style={{ width: "auto" }} value={head.from}>
                      {meetingTimeOptions.map((time) => (
                        <option key={time} value={time}>
                          {time}
                        </option>
                      ))}
                    </select>
                    <span className="t-meta" style={{ flex: "none" }}>
                      ~
                    </span>
                    <select aria-label="종료 시각" onChange={(event) => setHead({ ...head, to: event.target.value })} style={{ width: "auto" }} value={head.to}>
                      {meetingTimeOptions.map((time) => (
                        <option key={time} value={time}>
                          {time}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
                {/* 장소는 이름표다 — 회의실 판정·예약은 여기로 오지 않는다 (D14) */}
                <div className="field">
                  <label htmlFor="meeting-head-place">{meetingScreen.place}</label>
                  <input
                    id="meeting-head-place"
                    onChange={(event) => setHead({ ...head, place: event.target.value })}
                    placeholder={meetingScreen.placeField}
                    type="text"
                    value={head.place}
                  />
                </div>
              </div>

              <div className="field" style={{ minWidth: 0 }}>
                <label>
                  {meetingScreen.attendees} <span className="count-badge">{head.people.length}</span>
                </label>
                <PickedTags
                  items={head.people.map((person) => ({
                    key: person.member_id,
                    name: personName(person.display_name),
                    onRemove: () => setHead({ ...head, people: head.people.filter((one) => one.member_id !== person.member_id) }),
                  }))}
                />
                <div style={{ marginTop: 12 }}>
                  <PersonSearch
                    excluded={headPeopleIds}
                    onPick={(person: RosterPerson) => {
                      setHead({ ...head, people: [...head.people, { member_id: person.member_id, display_name: person.name }] });
                      setHeadQuery("");
                    }}
                    onQueryChange={setHeadQuery}
                    placeholder={meetingScreen.nameSearchPlaceholder}
                    query={headQuery}
                    roster={roster}
                  />
                </div>
              </div>
            </div>
          ) : (
            <div>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <h1 className="meeting-page-title">{meeting.title ?? meetingScreen.noTitle}</h1>
                {/* 합성이 낸 제목 후보 — 아직 제목이 아니다. 연필로 열어 저장해야 제목이 된다 */}
                {!meeting.title && meeting.title_candidate && (
                  <span className="t-meta" style={{ fontSize: 12 }}>
                    {meetingScreen.titleCandidate(meeting.title_candidate)}
                  </span>
                )}
                {/* E02 상태 배지 — 정리 중 · 실패 · 취소됨만 */}
                {badgeClass && status && <span className={badgeClass}>{meetingStatusLabel[status]}</span>}
              </div>
              <p style={{ fontSize: 13 }}>{facts}</p>
            </div>
          )}
        </div>

        <div className="page-head-actions">
          {(planned || cancelled) && attendee && (
            /* 고치다 만 값으로 회의를 열지 않는다 — 편집 중에는 비활성이다 */
            <button className="btn primary" disabled={headEdit || busy} onClick={() => void run(() => startMeeting(meeting.meeting_id))} type="button">
              <Icon name="play" size={14} /> {meetingScreen.start}
            </button>
          )}
          {/* 회의를 닫는 것은 이끄는 창의 일이다 — 보기만 하는 창에는 서지 않는다 */}
          {hosting && (
            <button className="btn primary" disabled={busy} onClick={() => void run(() => endMeeting(meeting.meeting_id))} type="button">
              {meetingScreen.end}
            </button>
          )}
          {/* 공유는 «끝난 뒤» 에만 선다 (D34) — 진행 중에도 정리 중에도 아직 읽을 회의록이 없다 */}
          {attendee && (settled || failedState) && (
            <button className="btn" onClick={() => setShare(true)} type="button">
              {meetingScreen.share}
            </button>
          )}
          {/* 받는 것은 브라우저가 한다 — 서버가 Content-Disposition 을 실어 보낸다. 형식은 HTML 하나다 */}
          {settled && (
            <a className="btn" href={meetingExportUrl(meeting.meeting_id)}>
              {meetingScreen.export}
            </a>
          )}
          {settled && attendee && (
            <button className="btn" onClick={() => setBooking(true)} type="button">
              {meetingScreen.bookNext}
            </button>
          )}
        </div>
      </div>

      {/* E54 변환 실패 안내 + E55 [다시 시도] */}
      {failedState && (
        <div className="effect-note meeting-failed-note">
          <span style={{ fontSize: 13, color: "var(--text-primary)" }}>
            {meetingScreen.convertFailed}
            {meeting.failure_reason && <span className="t-meta" style={{ marginLeft: 8, fontSize: 12 }}>{meeting.failure_reason}</span>}
          </span>
          {/* 합성만 다시 건다 — 받은 발화와 메모는 건드리지 않는다 */}
          {canEditNote && (
            <button
              className="btn h30"
              disabled={busy}
              onClick={() => void run(() => retryMeetingFinalize(meeting.meeting_id))}
              style={{ flex: "none" }}
              type="button"
            >
              {meetingScreen.retry}
            </button>
          )}
        </div>
      )}

      {/* E85 회의 목적 — 두 칸 위 전체 폭 한 줄 (X-198). 목적이 안건 바를 대신한다 */}
      {meeting.purpose && (
        <div className="effect-note meeting-purpose">
          <span className="t-meta" style={{ flex: "none", width: 30, fontSize: 12, paddingTop: 1 }}>
            {meetingScreen.purpose}
          </span>
          <span style={{ fontSize: 13, fontWeight: 700, color: "var(--text-primary)", textWrap: "pretty" }}>{meeting.purpose}</span>
        </div>
      )}

      <div className={rightShown ? "meeting-columns detail" : "meeting-columns detail single"}>
        <section aria-label={meetingScreen.noteHead} className="meeting-panel">
          <header className="meeting-note-head">
            <div style={{ display: "flex", alignItems: "baseline", gap: 10, minWidth: 0 }}>
              <h2>{meetingScreen.noteHead}</h2>
              {live && (
                <span className="t-meta tabular" style={{ fontSize: 12 }}>
                  {meetingScreen.summarizingAt(meetingClock(meeting.starts_at))}
                </span>
              )}
              {/* 배지를 두지 않는다 (D34) — 이 한 줄과 아래의 로딩이 이미 그 사실을 말한다 */}
              {settling && (
                <span className="t-meta" style={{ fontSize: 12 }}>
                  {meetingScreen.summarizingNow}
                </span>
              )}
              {settled && !noteEditing && meeting.last_saved_at && (
                <span className="t-meta tabular" style={{ fontSize: 12 }}>
                  {meetingScreen.lastSaved(meetingClock(meeting.last_saved_at))}
                </span>
              )}
            </div>
            {canEdit && (
              <button
                className="btn h30"
                disabled={busy}
                onClick={() => (noteEditing ? void saveNote() : setEditing(true))}
                style={{ flex: "none" }}
                type="button"
              >
                {noteEditing ? meetingScreen.save : meetingScreen.edit}
              </button>
            )}
          </header>

          {leftTabs && (
            <div className="meeting-tabs">
              <button className={memoTab ? "on" : ""} onClick={() => setLeft("memo")} type="button">
                {meetingScreen.tabMemo}
              </button>
              <button className={aiTab ? "on" : ""} onClick={() => setLeft("ai")} type="button">
                {meetingScreen.tabAi}
              </button>
            </div>
          )}

          <div className="meeting-scroll" ref={noteScroll} style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: "18px 20px" }}>
            {settling ? (
              <Skeleton label="회의록을 정리하는 중" rows={7} />
            ) : (
              shownAgendas.map((agenda, index) => (
                <AgendaBlock
                  edit={
                    lineEditing
                      ? {
                          lines: draftOf(agenda),
                          onAdd: () => setBodies((current) => ({ ...current, [agenda.agenda_id]: [...draftOf(agenda), ""] })),
                          onChange: (at, value) =>
                            setBodies((current) => ({ ...current, [agenda.agenda_id]: draftOf(agenda).map((text, k) => (k === at ? value : text)) })),
                          onRemove: (at) =>
                            setBodies((current) => ({ ...current, [agenda.agenda_id]: draftOf(agenda).filter((_, k) => k !== at) })),
                        }
                      : null
                  }
                  index={index}
                  key={agenda.agenda_id}
                  lines={
                    planned || cancelled
                      ? []
                      : memoTab
                        ? // 이 세션에서 던진 메모는 돌아온 줄만 뒤에 붙는다 — 낙관 렌더를 하지 않는다
                          [...tracked(agenda, "memo"), ...memosOf(agenda.agenda_id).map((text) => ({ text }))]
                        : aiTab
                          ? // 배치가 실어 온 것이 곧 AI 트랙 전체다 — 그 안의 줄은 track 값을 따지지 않는다
                            lineViews(stream.batch ? agenda.lines : agenda.lines.filter((line) => line.track === "ai"))
                          : /* 회의록 줄 — 그 줄이 딛는 구간의 «시작 시각 하나» 를 줄 오른쪽에 낸다 (D34).
                               누르면 스크립트의 그 자리가 열린다. 근거가 없는 줄은 그 자리가 빈다 */
                            tracked(agenda, "final")
                  }
                  /* E22 결론 표시 — 완료에만. 합성이 안 된 「실패」에는 AI 가 낸 값 자체가 없다 */
                  mark={settled ? { text: agenda.concluded ? meetingScreen.concluded : meetingScreen.notConcluded, concluded: agenda.concluded } : null}
                  onRemove={agendaEditing ? () => setAskRemoveAgenda(agenda) : null}
                  source={meetingAgendaSourceText(agenda.source)}
                  title={agenda.title}
                  todos={
                    /* 회의 «중» 후속 업무 후보 (D46) — 「AI 요약」 탭에만, 읽기 전용이다.
                       배치가 온 뒤에는 그 회차가 낸 것 전량이고(AI 트랙과 같은 결로 통째 교체),
                       배치 전에 들어온 창은 상세가 실어 온 잠정 후보를 세운다. 조작 자리는 두지 않는다 —
                       승격도 후보 빼기도 종료 뒤 최종에서만 한다. 메모 탭에는 서지 않는다. */
                    aiTab
                      ? (stream.batch ? agenda.todos : agenda.todos.filter((todo) => todo.provisional)).map((todo) => ({
                          key: todo.todo_id,
                          what: todo.title,
                          due: todo.due_candidate ?? undefined,
                        }))
                      : settled && !noteEditing
                        ? agenda.todos.map((todo) => ({
                            key: todo.todo_id,
                            what: todo.title,
                            /* 담당 후보 칸은 없다 (D19-3) — 기한 후보만, 그것도 있을 때만 낸다 */
                            due: todo.due_candidate ?? undefined,
                            /* 이미 요청으로 선 줄은 목록에서 빼지 않고 「요청됨」으로 남는다 — 누르는 자리는
                               두지 않는다 (D24). `linked` 는 데이터로만 쓴다.
                               아직 후보인 줄에는 [업무 생성]과 후보 빼기가 함께 선다 */
                            actions: todo.linked ? (
                              <span className="t-meta" style={{ fontSize: 12 }}>
                                {meetingScreen.requested}
                              </span>
                            ) : attendee ? (
                              <>
                                {canCreateWorkRequests && (
                                  <button className="btn h30" onClick={() => setPromoting(todo)} type="button">
                                    {meetingScreen.promote}
                                  </button>
                                )}
                                {/* E73 후보 삭제 — 확인을 묻지 않는다. 아직 업무가 아니다 */}
                                <button
                                  aria-label={meetingScreen.dropTodo}
                                  className="btn ghost icon h30"
                                  onClick={() => void run(() => removeMeetingTodo(meeting.meeting_id, todo.todo_id))}
                                  style={{ width: 30, height: 30, color: "var(--text-tertiary)" }}
                                  type="button"
                                >
                                  <Icon name="close" size={14} />
                                </button>
                              </>
                            ) : null,
                          }))
                      : null
                  }
                />
              ))
            )}

            {/* E35 [안건 추가] — 편집 중에만. 예정·취소됨(안건 편집)과 완료·실패(회의록 편집) 양쪽에 선다 */}
            {agendaEditing && (
              <div className="meeting-add-agenda">
                <input
                  aria-label={meetingScreen.agendaPlaceholder}
                  onChange={(event) => setAgendaDraft(event.target.value)}
                  placeholder={meetingScreen.agendaPlaceholder}
                  type="text"
                  value={agendaDraft}
                />
                <button
                  className="btn"
                  disabled={agendas.length >= 20 || agendaDraft.trim().length === 0 || busy}
                  onClick={() =>
                    void run(async () => {
                      await addMeetingAgenda(meeting.meeting_id, agendaDraft.trim());
                      setAgendaDraft("");
                    })
                  }
                  type="button"
                >
                  {meetingScreen.addAgenda}
                </button>
              </div>
            )}
          </div>

          {/* E80·E81 — 「진행 중」에만, 쓰는 사람은 회의를 만든 사람 하나다 (SPEC §6-1).
              그 밖의 참석자는 같은 목록을 읽기로 본다 */}
          {/* 업스트림 자리를 가진 창이 따로 있으면 여기서는 메모도 올리지 않는다 — 마이크와 같은 규칙이다 */}
          {live && hosting && (
            <MemoComposer
              agendas={agendas}
              meetingId={meeting.meeting_id}
              /* 회의 중 안건 세우기 — 세운 뒤 상세를 다시 읽어 번호와 순서를 서버가 준 대로 맞춘다 */
              onCreateAgenda={async (title) => {
                const created = await addMeetingAgenda(meeting.meeting_id, title);
                await reload();
                return created;
              }}
              onSaved={(agendaId, line) =>
                setSessionMemos((current) => [...current, { agendaId, line }])
              }
            />
          )}
        </section>

        {rightShown && (
          <section aria-label="자료와 스크립트" className="meeting-panel">
            <header className="meeting-tabs" style={{ borderBottom: "1px solid var(--border-default)" }}>
              {tabMaterials && (
                <button className={materialsOn ? "on" : ""} onClick={() => setRight("materials")} type="button">
                  {meetingScreen.tabMaterials}
                </button>
              )}
              {tabScript && (
                <button className={scriptOn ? "on" : ""} onClick={() => setRight("script")} type="button">
                  {meetingScreen.tabScript}
                </button>
              )}
            </header>
            <div className="meeting-scroll" style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: "16px 20px" }}>
              {materialsOn ? (
                <div>
                  {materials === null ? (
                    <Skeleton label="자료를 불러오는 중" rows={3} />
                  ) : materials.length === 0 ? (
                    <div style={{ padding: "60px 0" }}>
                      <Empty title={meetingScreen.materialsEmpty} />
                    </div>
                  ) : (
                    <FileList
                      label={meetingScreen.tabMaterials}
                      rows={materials.map((file) => ({
                        key: file.material_id,
                        name: file.name,
                        size: sizeText(file.size),
                        active: file.material_id === openMaterial,
                        onOpen: () => setOpenMaterial(file.material_id),
                        /* E75 자료 삭제 — 떼는 것은 올린 사람이고 「진행 중」에는 아무도 못 뗀다.
                           그 둘을 합친 판정은 **서버가 낸다** — 화면이 다시 맞춰 보지 않는다 */
                        removeLabel: meetingScreen.attachDropFile,
                        onRemove: file.can_detach ? () => setAskDropMaterial(file) : undefined,
                      }))}
                    />
                  )}
                  {/* E06 [자료 첨부] — 「진행 중」에는 숨긴다 (X-117) */}
                  {attendee && !live && (
                    <button className="btn" onClick={() => setAttach(true)} style={{ width: "100%", marginTop: 12 }} type="button">
                      <Icon name="paperclip" size={14} /> {meetingScreen.attach}
                    </button>
                  )}
                </div>
              ) : scriptRows.length > 0 ? (
                <LiveScript rows={scriptRows} />
              ) : (
                <Empty title={meetingScreen.scriptEmpty} />
              )}
            </div>
          </section>
        )}
      </div>

      {/* E64 자료 미리보기 — 고른 자료 하나를 드로어로 연다. 탭 안에 미리보기를 깔지 않는다 */}
      {openMaterial && materials && (
        <MaterialDrawer
          material={materials.find((one) => one.material_id === openMaterial) ?? null}
          meetingId={meeting.meeting_id}
          onClose={() => setOpenMaterial(null)}
        />
      )}

      {share && <ShareModal meeting={meeting} onClose={() => setShare(false)} onNotice={onNotice} />}
      {/* [업무 생성] — 현행 업무 요청 모달을 그대로 쓴다. 제목·설명·기한·체크리스트만 옮겨 담고
          담당 후보는 비워 연다. 후보 목록은 회의 참석자를 앞에 세운다 */}
      {promoting && (
        <CreateWorkDrawer
          assigneeCandidates={orderedAssignees}
          canCreateRequest
          canCreateTask={false}
          ccCandidates={ccCandidates}
          initial={{
            title: promoting.title,
            description: promoting.description,
            dueDate: promoting.due_candidate,
            checklist: promoting.checklist_candidate,
          }}
          onClose={() => setPromoting(null)}
          onCreated={async (message) => {
            setPromoting(null);
            await reload();
            onNotice(message);
          }}
          onError={onError}
          /* 승격은 언제나 업무 요청이다 (§9-5). 회의록에서 나가는 요청만 이 자리를 쓴다 —
             출처 두 열(회의·안건)이 함께 실려야 같은 후보가 두 번 서지 않는다 */
          onSubmitRequest={async (input) => {
            const todoId = promoting.todo_id;
            try {
              await promoteMeetingTodo(meeting.meeting_id, todoId, input);
            } catch (reason) {
              // 이미 누가 보낸 후보다 — 지금 있는 것을 다시 읽어 「요청됨」으로 맞춘다
              if (reason instanceof ApiError && reason.status === 409) {
                await reload();
                return meetingScreen.alreadyRequested;
              }
              throw reason;
            }
            return meetingScreen.promoted(input.title);
          }}
          ownerName={ownerName}
        />
      )}

      {booking && (
        <BookingModal
          carriedFrom={meeting.meeting_id}
          initialAgendas={record.agendas.filter((agenda) => !agenda.concluded).map((agenda) => ({ title: agenda.title, source: "carried" as const }))}
          initialSubject={meeting.title ?? ""}
          onClose={() => setBooking(false)}
          onCreated={(next) => {
            setBooking(false);
            const notice = roomReservationNotice(next);
            if (notice) onNotice(notice);
            onOpenMeeting(next.meeting.meeting_id);
          }}
          onError={(message) => onError(message)}
          onNotice={onNotice}
          pastRows={[]}
        />
      )}
      {attach && (
        <AttachModal
          meetingId={meeting.meeting_id}
          onAttached={(count, partial) => {
            void loadMaterials();
            // 일부만 올라가면 모달을 닫지 않는다 — 못 붙은 것이 사유와 함께 남아 있어야 한다
            if (!partial) setAttach(false);
            if (count > 0) onNotice(meetingScreen.attached);
          }}
          onClose={() => setAttach(false)}
        />
      )}

      {/* T27 안건 지우기 확인 */}
      {askRemoveAgenda && (
        <div className="modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && setAskRemoveAgenda(null)}>
          <section aria-label={meetingScreen.agendaRemoveTitle} aria-modal="true" className="modal" role="alertdialog">
            <header className="modal-head">
              <h3>{meetingScreen.agendaRemoveTitle}</h3>
            </header>
            <footer className="modal-foot">
              <button className="btn h40 ghost" onClick={() => setAskRemoveAgenda(null)} type="button">
                {meetingScreen.keep}
              </button>
              <button
                className="btn h40 danger"
                disabled={busy}
                onClick={() => {
                  const target = askRemoveAgenda;
                  setAskRemoveAgenda(null);
                  void run(() => removeMeetingAgenda(meeting.meeting_id, target.agenda_id), meetingScreen.agendaRemoved);
                }}
                type="button"
              >
                {meetingScreen.remove}
              </button>
            </footer>
          </section>
        </div>
      )}

      {/* T29 자료 삭제 확인 */}
      {askDropMaterial && (
        <div className="modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && setAskDropMaterial(null)}>
          <section aria-label={meetingScreen.materialRemoveTitle} aria-modal="true" className="modal" role="alertdialog">
            <header className="modal-head">
              <h3>{meetingScreen.materialRemoveTitle}</h3>
            </header>
            <footer className="modal-foot">
              <button className="btn h40 ghost" onClick={() => setAskDropMaterial(null)} type="button">
                {meetingScreen.keep}
              </button>
              <button
                className="btn h40 danger"
                disabled={busy}
                onClick={() => {
                  const target = askDropMaterial;
                  setAskDropMaterial(null);
                  if (openMaterial === target.material_id) setOpenMaterial(null);
                  void run(async () => {
                    // 이 회의에서의 연결만 끊는다 — 자료 모듈에 쌓인 것은 남는다 (X-160)
                    await detachMeetingMaterial(meeting.meeting_id, target.material_id);
                    await loadMaterials();
                  }, meetingScreen.materialRemoved);
                }}
                type="button"
              >
                {meetingScreen.remove}
              </button>
            </footer>
          </section>
        </div>
      )}

      {/* T11 저장하지 않고 이탈 */}
      {askLeave && (
        <div className="modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && setAskLeave(null)}>
          <section aria-label={meetingScreen.leaveTitle} aria-modal="true" className="modal" role="alertdialog">
            <header className="modal-head">
              <h3>{meetingScreen.leaveTitle}</h3>
            </header>
            <footer className="modal-foot">
              <button className="btn h40 ghost" onClick={() => setAskLeave(null)} type="button">
                {meetingScreen.keep}
              </button>
              <button
                className="btn h40"
                onClick={() => {
                  const proceed = askLeave;
                  setAskLeave(null);
                  setEditing(false);
                  setHeadEdit(false);
                  proceed();
                }}
                type="button"
              >
                {meetingScreen.leave}
              </button>
            </footer>
          </section>
        </div>
      )}
    </section>
  );
}

/**
 * 연필 — SCAX `Icon` 26종에 `pencil` 이 없다. 세트에 없는 글리프를 이 화면에서만 같은 규격으로 그린다
 * (16 박스 · `fill:none` · `stroke:currentColor` · 1.3 · round). 채운 아이콘은 만들지 않는다.
 * DS 에 `pencil` 이 들어오면 `<Icon name="pencil" />` 한 줄로 갈아 끼운다 (시안 리포트 M14).
 */
function PencilGlyph() {
  return (
    <svg aria-hidden fill="none" height="14" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.3" viewBox="0 0 16 16" width="14">
      <path d="M11.3 2.7l2 2-7.9 7.9-2.6.6.6-2.6z" />
      <path d="M9.9 4.1l2 2" />
    </svg>
  );
}
