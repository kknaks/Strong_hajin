import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

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
} from "../../lib/api";
import { Badge } from "../../ds/Badge";
import { Button, IconButton } from "../../ds/Button";
import { SegmentedControl } from "../../ds/SegmentedControl";
import { Empty } from "../../ds/Empty";
import { FileList } from "../../ds/FileList";
import { Icon } from "../../ds/icons/Icon";
import { Drawer } from "../../ds/Modal";
import { Skeleton } from "../../ds/Skeleton";
import { Spinner } from "../../ds/Spinner";
import { StatusNote } from "../../ds/StatusNote";
import { emptyActionLabel, meetingAgendaSourceText, meetingBadgeTone, meetingClock, meetingDateInput, meetingElapsed, meetingIsoAt, meetingRange, meetingScreen, meetingSpeakerName, meetingStatusLabel, meetingTimeOptions, personName } from "../../lib/labels";
import type {
  MeetingAgenda,
  MeetingEvidence,
  MeetingLine,
  MeetingMaterial,
  MeetingRecord,
  MeetingTodo,
  MeetingTranscript,
  Persona,
} from "../../lib/viewModels";
import { AgendaBlock, type AgendaLineView } from "./AgendaBlock";
import { AttachModal, failureText, sizeText } from "./AttachModal";
import { MaterialDrawer } from "./MaterialDrawer";
import { LiveScript, type ScriptRow } from "./LiveScript";
import { MemoComposer } from "./MemoComposer";
import { useMeetingStream, type StreamClosure } from "./stream";
import { BookingModal, roomReservationNotice } from "./BookingModal";
import { PersonSearch, PickedTags } from "./PeoplePicker";
import { ShareModal } from "./ShareModal";
import { CreateWorkModal } from "../work/WorkModals";
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
  sideRailHost,
  focus = false,
  onToggleFocus,
  onRegisterRefresh,
  reloadToken = 0,
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
  /**
   * 바퀴 6a M-5: 첨부·스크립트 칸은 셸의 «4칸» 에 선다.
   * 만들어진 노드를 위로 올리지 않고 «어디에 그릴지» 를 받아 그 자리에 포털로 그린다 —
   * 노드를 올리면 렌더마다 새 노드라 등록 → 상위 setState → 재렌더가 끝없이 돈다.
   * 포털이면 이 화면이 자기 리듬대로 다시 그리고, DOM 만 4칸 안에 앉는다.
   */
  sideRailHost?: HTMLElement | null;
  /** 바퀴 6a M-5: 켜면 목록 칸이 사라지고 상세가 넓어진다. 접는 판단은 워크스페이스가 한다. */
  focus?: boolean;
  onToggleFocus?: () => void;
  onRegisterRefresh?: (refresh: (() => Promise<void>) | null) => void;
  /**
   * 값이 바뀌면 상세를 다시 읽는다 — 목록 칸의 [수정]이 이 회의의 정보를 고쳤을 때 쓴다.
   * 다시 마운트하지 않는 이유가 있다: 고치던 회의록 줄과 읽던 자리의 스크롤을 잃지 않는다.
   */
  reloadToken?: number;
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

  /* 바깥(목록의 회의 정보 모달)이 이 회의를 고쳤다 — 첫 렌더에는 돌지 않는다(위 effect 가 이미 읽었다) */
  const firstReloadToken = useRef(reloadToken);
  useEffect(() => {
    if (reloadToken === firstReloadToken.current) return;
    void reload().catch(() => {
      /* 한 번 못 읽었다고 화면을 오류로 덮지 않는다 — 지금 있는 값이 그대로 서 있다 */
    });
  }, [reload, reloadToken]);

  /* 끝난 회의의 원문은 서버가 갖고 있다 — 「진행 중」만 스트림에서 오고 나머지는 이 자리를 읽는다.
     예정·취소됨에는 탭 자체가 서지 않으므로 읽지 않는다 (§5.4-7). */
  /* 「진행 중」에도 읽는다 — 중간에 들어온 사람은 이미 적재된 확정 블록부터 봐야 한다.
     주최자 창에서는 그 뒤로 스트림의 `transcript.final` 이 이어 붙고, 스트림이 없는 창은 폴링이 따라간다. */
  /* 상태가 바뀌면 그때마다 다시 읽는다 — 「참」이 「참」으로 남는 조건이면 한 번 읽고 마는데,
     끝난 회의의 원문은 서버가 재전사로 통째 갈아 끼우므로(in_progress 때 본 것과 다른 글이다)
     in_progress→summarizing→done 마디마다 새로 읽어야 그 글이 화면에 온다 (D2). */
  /* 상태가 바뀔 때마다 다시 읽는다. 「예정」·「취소됨」을 빼 두었던 조건은 걷었다 — 그 회의도
     스크립트 탭이 서고, 서버는 아직 아무 말도 없는 회의에 «빈 목록» 을 준다. 못 읽는 회의와
     아직 말이 없는 회의를 화면이 같은 모양으로 내지 않으려면 실제로 물어봐야 한다. */
  const transcriptStatus = record?.meeting.status ?? null;
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

  /* 고치던 것 — 회의 정보는 목록의 모달이 자기 닫기 경로에서 스스로 지키므로(MeetingEditModal),
     이 화면이 들고 있는 「저장 안 한 것」은 회의록 편집 하나다. */
  const dirty = editing;
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
  /*
   * 회의 «전» 의 안건은 편집 모드를 거치지 않는다 (시안 10 · `workspace.v1.jsx:221`).
   * 시안은 안건 목록 바로 아래에 입력칸과 [안건 추가]를 그냥 세워 둔다 — 예정 회의에서 [수정]이
   * 하던 일이 «그 칸을 펴는 것» 하나였기 때문이다. 그래서 그 상태에서는 칸을 늘 펴 두고 [수정]을
   * 내린다: 누르는 걸음이 하나 줄 뿐 **할 수 있는 일은 그대로다** — 안건 빼기(×)도 같이 상시로 선다.
   * 「취소됨」이 함께 걸리는 것은 그 화면이 「예정」과 같은 안건 목록이기 때문이다(§5.7).
   * 권한은 여전히 서버가 말한다 — `can_edit_agendas` 가 닫혀 있으면 칸 자체가 서지 않는다.
   */
  const agendaAlways = canEditAgendas && (planned || cancelled);
  /** 안건을 더하고 뺄 수 있는가 — 편집 모드 안이거나, 회의 전이라 늘 열려 있거나. */
  const agendaOpen = agendaEditing || agendaAlways;

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
  /* 자료는 참석자의 것이다 — 공유받은 사람에게는 그 탭이 서지 않는다 (E75 · 기존 계약).
     스크립트는 «읽을 수 있는 회의면 늘» 선다: 시안이 「예정 회의는 스크립트가 비어 있을 뿐」이라고
     적어 두었고, `GET /transcript` 도 상태를 가리지 않고 빈 목록을 낸다. 상태로 탭을 지우면
     「아직 없다」와 「볼 수 없다」가 같은 모양이 된다. */
  const tabMaterials = attendee;
  const materialsOn = tabMaterials && right === "materials";
  /* 세그먼티드에 세울 항목. 스크립트는 «늘» 서므로 조건이 없다 — 자료만 참석자에게 갈린다.
     말은 labels 가 준다 (부품은 말을 모른다). */
  const railOptions = [
    ...(tabMaterials ? [{ value: "materials" as const, label: meetingScreen.tabMaterials }] : []),
    { value: "script" as const, label: meetingScreen.tabScript },
  ];


  const draftOf = useCallback(
    (agenda: MeetingAgenda) => bodies[agenda.agenda_id] ?? linesOf(agenda, "final"),
    [bodies],
  );

  if (failed) {
    return (
      <section className="page-surface meeting-surface">
        <div className="meeting-panel-center">
          <Empty
            actionLabel={emptyActionLabel.error} onAction={() => void reload().catch(() => setFailed(true))} title={meetingScreen.listError} variant="error" />
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

  /*
   * AI 트랙에 «실제로 들어온 것» 이 있는가 (§7).
   *
   * 이 값이 거짓인 동안 「AI 요약」 탭은 사람이 쓴 안건 제목과 빈 후보 상자를 대신 내고 있었다 —
   * AI 가 아직 아무것도 안 냈는데 화면은 뭔가 정리된 것처럼 보였다(현재 화면 24).
   *
   * 판정을 **stream.batch 유무로만 하지 않는다.** 배치는 이 «창» 이 붙어 있는 동안 온 것이라,
   * 새로고침하면 이미 저장된 AI 요약이 있어도 거짓이 된다. 그래서 상세 응답이 실어 온 것까지 함께 본다:
   *   · `track === "ai"` 인 줄이 하나라도 있는가 (§7.1 적재 — 배치 결과는 AI 트랙 줄로 저장된다)
   *   · 잠정 후보(`provisional`)가 하나라도 있는가 (같은 트랜잭션으로 함께 적재된다)
   *
   * **`source` 로 거르지 않는다.** AI 가 기존 안건(출처 「직접 입력」)을 요약한 경우도 유효한 결과다
   * (§7.1 「안건이 없으면 AI 가 만들고」 — 있으면 그 안건에 줄을 붙인다). 출처로 거르면 그 경우가 통째로 사라진다.
   */
  const aiTrackArrived =
    Boolean(stream.batch) ||
    agendas.some((agenda) => agenda.lines.some((line) => line.track === "ai") || agenda.todos.some((todo) => todo.provisional));
  /** 「AI 요약」 탭인데 아직 AI 가 낸 것이 없다 — 안건 목록 대신 기다리는 중임을 한 줄로 말한다. */
  const aiPending = aiTab && !aiTrackArrived;

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

  const badgeTone = status ? meetingBadgeTone[status] : undefined;
  const facts = [meetingRange(meeting.starts_at, meeting.ends_at), meeting.location, meetingScreen.attendCount(meeting.attendees.length + meeting.external_attendees.length)]
    .filter(Boolean)
    .join(" · ");
  /* 담당 후보는 회의 참석자를 앞에 세운다 — 그 자리에 있던 사람이 먼저 걸린다. 목록 자체는 서버가 준 것 그대로다 */
  const attendeeIds = new Set(meeting.attendees.map((one) => one.member_id));
  const orderedAssignees = [
    ...assigneeCandidates.filter((one) => attendeeIds.has(one.id)),
    ...assigneeCandidates.filter((one) => !attendeeIds.has(one.id)),
  ];

  /*
   * 4칸: 첨부 · 스크립트 (시안 08 · `workspace.v1.jsx:239`).
   *
   * 걷어낸 것 셋:
   *   · 열 전체를 감싸던 `.meeting-panel`(1px 테두리 + 16 라운드). 시안의 이 열은 카드가 아니라
   *     **칸**이다 — 셸이 그어 둔 왼쪽 세로 경계선 하나로 갈리고 화면 바닥까지 이어진다.
   *   · 「자료」 밑줄 탭. 시안은 회색 바탕에 고른 것만 흰 세그먼티드고, 항목은 늘 둘이다.
   *   · 자료가 없을 때 열을 통째로 채우던 큰 빈 상태 카드. 빈 것은 한 줄로 말하고 [+ 자료 첨부]는
   *     **목록 바로 아래** 제자리에 선다 — 짧은 카드 바닥에 붙지 않는다.
   *
   * **스크립트 탭은 상태와 무관하게 선다** — 시안 주석이 직접 그렇게 적어 두었고
   * (「예정 회의는 스크립트가 비어 있을 뿐이다」), 계약도 그 말과 맞는다:
   * `GET /api/meetings/{id}/transcript` 는 읽을 수 있는 회의면 아직 아무 말도 없어도 빈 목록을 낸다
   * (`backend/.../meetings/application.py:1049`). 그래서 빈 상태는 지어낸 것이 아니라 사실이다.
   */
  const sideRail = (
    <section aria-label={meetingScreen.sideRailLabel} className="scax-side-rail">
      <header className="scax-side-rail__header">
        <SegmentedControl
          ariaLabel={meetingScreen.sideRailLabel}
          onChange={setRight}
          options={railOptions}
          value={materialsOn ? "materials" : "script"}
        />
      </header>
      <div className="scax-side-rail__body scax-scroll">
        {materialsOn ? (
          <>
            {materials === null ? (
              <Skeleton label="자료를 불러오는 중" rows={3} />
            ) : materials.length === 0 ? (
              <p className="scax-side-rail__empty">{meetingScreen.materialsEmpty}</p>
            ) : (
              <FileList
                /* 목록의 이름은 «무엇의 목록인가» 다 — 탭의 낱말(「첨부」)과 따로 둔다 */
                label={meetingScreen.materials}
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
            {/* E06 [+ 자료 첨부] — 「진행 중」에는 숨긴다 (X-117). 자리는 목록 바로 아래다 */}
            {attendee && !live && (
              <Button onClick={() => setAttach(true)} size="sm" style={{ width: "100%" }} type="button">
                <Icon name="plus" size={14} /> {meetingScreen.attach}
              </Button>
            )}
          </>
        ) : scriptRows.length > 0 ? (
          <LiveScript rows={scriptRows} />
        ) : (
          <p className="scax-side-rail__empty">{meetingScreen.scriptEmpty}</p>
        )}
      </div>
    </section>
  );

  return (
    <section className="scax-detail">
      {sideRailHost ? createPortal(sideRail, sideRailHost) : null}
      {/* E12 진행 표시 — 회의가 도는 동안 위에 남는다 */}
      {live && (
        <div className="scax-live-bar">
          <span style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <Badge tone="accent">{meetingStatusLabel.in_progress}</Badge>
            <span className="scax-live-bar__elapsed">{elapsed}</span>
            {streamNotice && (
              <StatusNote tone={stream.phase === "closed" && !takenElsewhere ? "danger" : "muted"}>{streamNotice}</StatusNote>
            )}
            {stream.micDenied && <StatusNote tone="danger">{meetingScreen.micDenied}</StatusNote>}
          </span>
          <span className="scax-live-bar__who">{meetingScreen.startedBy(personName(meeting.created_by))}</span>
        </div>
      )}

      {/*
        * 시안 10 의 상세 머리 — **제목부터 시작한다.**
        *   1줄: 제목 (+ 상태 배지 셋) ······ [확장 화살표] [▷ 회의 시작]
        *   2줄: 일시 · 장소 · 참석 N명
        *
        * 걷어낸 것 둘:
        *   · 제목 «위» 의 「회의 정보」 구획 라벨과 그 옆 연필. 고치는 자리는 목록 카드의
        *     [수정] → `MeetingEditModal` 로 옮겼다(시안 02). 칸도 저장 경로도 그대로다.
        *   · [회의 시작]이 제목 아래 «따로 한 줄» 로 서던 배치. 시안은 제목과 같은 줄 오른쪽이고,
        *     보라 면이 아니라 흰 면 + 1px 선이다 — 화면 맨 위의 [빠른 시작]과 결이 갈린다.
        * 집중 모드 글리프도 시안 것으로 갔다: 네모에 세로선(`left-side`)이 아니라 대각선 양방향
        * 화살표(`expand`/`collapse`)이고, 자리는 [회의 시작] «왼쪽» 이다.
        */}
      <header className="scax-detail__head">
        <div className="scax-detail__title-row">
          <h2 className="scax-detail__title">{meeting.title ?? meetingScreen.noTitle}</h2>
          {/* 합성이 낸 제목 후보 — 아직 제목이 아니다. 목록의 [수정]으로 열어 저장해야 제목이 된다 */}
          {!meeting.title && meeting.title_candidate && (
            <span className="t-meta" style={{ fontSize: 12 }}>
              {meetingScreen.titleCandidate(meeting.title_candidate)}
            </span>
          )}
          {/* E02 상태 배지 — 정리 중 · 실패 · 취소됨만 */}
          {badgeTone && status && <Badge tone={badgeTone}>{meetingStatusLabel[status]}</Badge>}

          <div className="scax-detail__head-actions">
            {/* 바퀴 6a M-5: 회의에 집중하기 — 켜면 «목록 칸» 이 사라진다. 첨부 칸은 그대로 선다.
                시안(workspace.v1.jsx:177)이 쓰는 글리프를 그대로 들였다 — DS 원본에 있던 둘이다. */}
            {onToggleFocus && (
              <IconButton
                label={focus ? meetingScreen.exitFocus : meetingScreen.focus}
                name={focus ? "collapse" : "expand"}
                onClick={onToggleFocus}
                size={20}
              />
            )}
            {(planned || cancelled) && attendee && (
              <Button disabled={busy} onClick={() => void run(() => startMeeting(meeting.meeting_id))} size="sm" type="button">
                <Icon name="play" size={14} /> {meetingScreen.start}
              </Button>
            )}
            {/* 회의를 닫는 것은 이끄는 창의 일이다 — 보기만 하는 창에는 서지 않는다.
                **빨간 solid 를 그대로 둔다** (2026-09-14 사용자 정정). 시안 22 는 이 자리를 채움 없는
                글자로 그렸지만, 회의를 닫는 것은 되돌릴 수 없는 걸음이라 그만큼 눈에 띄어야 한다는
                판단이다. DS 의 solid-danger 그대로이고 동작·권한(hosting)·확인 흐름도 그대로다. */}
            {hosting && (
              <Button disabled={busy} onClick={() => void run(() => endMeeting(meeting.meeting_id))} size="sm" tone="danger" type="button" variant="solid">
                {meetingScreen.end}
              </Button>
            )}
            {/* 공유는 «끝난 뒤» 에만 선다 (D34) — 진행 중에도 정리 중에도 아직 읽을 회의록이 없다 */}
            {attendee && (settled || failedState) && (
              <Button onClick={() => setShare(true)} size="sm" type="button">
                {meetingScreen.share}
              </Button>
            )}
            {/* 받는 것은 브라우저가 한다 — 서버가 Content-Disposition 을 실어 보낸다. 형식은 HTML 하나다 */}
            {settled && (
              <a className="scax-button scax-button--outlined-neutral" href={meetingExportUrl(meeting.meeting_id)}>
                {meetingScreen.export}
              </a>
            )}
            {settled && attendee && (
              <Button onClick={() => setBooking(true)} size="sm" type="button">
                {meetingScreen.bookNext}
              </Button>
            )}
          </div>
        </div>
        <p className="scax-detail__facts">{facts}</p>
      </header>

      {/* E54 변환 실패 안내 + E55 [다시 시도] */}
      {failedState && (
        <div className="scax-detail__note-bar" role="alert">
          <span style={{ fontSize: 13, color: "var(--scax-color-ink)" }}>
            {meetingScreen.convertFailed}
            {meeting.failure_reason && <span className="t-meta" style={{ marginLeft: 8, fontSize: 12 }}>{meeting.failure_reason}</span>}
          </span>
          {/* 합성만 다시 건다 — 받은 발화와 메모는 건드리지 않는다 */}
          {canEditNote && (
            <Button size="sm" disabled={busy} onClick={() => void run(() => retryMeetingFinalize(meeting.meeting_id))}
              style={{ flex: "none" }}
              type="button"
            >
              {meetingScreen.retry}
            </Button>
          )}
        </div>
      )}

      {/* E85 회의 목적 — 두 칸 위 전체 폭 한 줄 (X-198). 목적이 안건 바를 대신한다 */}
      {meeting.purpose && (
        <div className="scax-detail__purpose">
          <span className="scax-detail__purpose-label">{meetingScreen.purpose}</span>
          <span className="scax-detail__purpose-text">{meeting.purpose}</span>
        </div>
      )}

      {/* 바퀴 6bc: 3칸은 시안대로 «흰 바닥에 테두리 없는 칸» 이다 — .meeting-panel(1px 테두리 + 16 라운드)을 벗었다.
          첨부 칸은 4칸 슬롯으로 나가 있다(6a M-5). */}
      <div aria-label={meetingScreen.noteHead} className="scax-note">
          {/*
            * 바퀴 6bc 6: 회의록 머리가 «두 줄»(제목 줄 + 밑줄 탭 줄)이던 것을 시안대로 «한 줄» 로 합쳤다.
            * 왼쪽은 늘 「AI 회의록」 고정, 오른쪽 자리만 상태별이다 —
            * 예정: 빈칸 · 진행 중: [메모|AI 요약] 세그먼티드 · 완료: [수정].
            */}
          <header className="scax-note__head">
            <div style={{ display: "flex", alignItems: "baseline", gap: 10, minWidth: 0 }}>
              <h3 className="scax-note__title">{meetingScreen.noteHead}</h3>
              {live && <span className="scax-note__meta tabular">{meetingScreen.summarizingAt(meetingClock(meeting.starts_at))}</span>}
              {/* 배지를 두지 않는다 (D34) — 이 한 줄과 아래의 로딩이 이미 그 사실을 말한다 */}
              {settling && <span className="scax-note__meta">{meetingScreen.summarizingNow}</span>}
              {settled && !noteEditing && meeting.last_saved_at && (
                <span className="scax-note__meta tabular">{meetingScreen.lastSaved(meetingClock(meeting.last_saved_at))}</span>
              )}
            </div>
            {/* 진행 중에만 메모/AI 요약이 갈린다 — 밑줄 탭이던 것을 세그먼티드로 (시안) */}
            {leftTabs && (
              <SegmentedControl
                ariaLabel={meetingScreen.noteHead}
                onChange={setLeft}
                options={[
                  { value: "memo" as const, label: meetingScreen.tabMemo },
                  { value: "ai" as const, label: meetingScreen.tabAi },
                ]}
                value={memoTab ? "memo" : "ai"}
              />
            )}
            {/* 회의 «전» 에는 서지 않는다 — 이 단추가 하던 일(안건 칸 펴기)을 상시 칸이 이미 하고 있다.
                줄을 고치는 상태(완료·실패)에서는 그대로다: 줄 편집은 저장을 명시로 눌러야 하는 일이라
                「고치는 중」이라는 상태가 화면에 남아야 한다. */}
            {canEdit && !agendaAlways && (
              <Button size="sm" disabled={busy} onClick={() => (noteEditing ? void saveNote() : setEditing(true))}
                style={{ flex: "none" }}
                type="button"
              >
                {noteEditing ? meetingScreen.save : meetingScreen.edit}
              </Button>
            )}
          </header>

          <div className="scax-note__body scax-scroll" ref={noteScroll}>
            {settling ? (
              /*
               * §8-2 「정리 중」 — 두 트랙을 합쳐 회의록 한 벌을 «새로 짓는» 동안이다.
               * 예전에는 스켈레톤 막대 일곱 줄이었다. 스켈레톤은 «올 내용의 모양을 아는» 자리인데
               * 합성 결과가 몇 줄일지는 아무도 모른다 — 그래서 화면이 「무엇을 기다리는지 말하지 않는
               * 긴 회색 줄」이 됐다(현재 화면 25). 도는 원과 한 문장으로 바꾼다.
               *
               * **이 자리는 시간이 지나면 스스로 걷힌다** — 프론트 타이머가 성공을 지어내지 않는다.
               * 위의 폴링(`SETTLING_POLL_MS`)이 상태를 다시 물어, 합성이 되면 「종료」가 되어 진짜
               * 회의록이 서고(§8-7), 재전사나 합성이 깨지면 「실패」가 되어 위의 실패 띠와 [다시 시도]가
               * 선다(§8-8). 어느 쪽이든 `settling` 이 거짓이 되므로 **영원히 도는 원은 없다.**
               */
              <div className="scax-note__settling">
                <Spinner label={meetingScreen.finalNoteGenerating} />
              </div>
            ) : aiPending ? (
              /* 사람이 쓴 안건과 빈 후보 상자를 AI 결과인 척 세우지 않는다 — 아직 없다고 말한다.
                 문구는 labels 가 갖는다. 「메모」 탭은 그대로라 적던 것은 그 자리에 그대로 있다. */
              <p className="scax-note__pending">{meetingScreen.aiSummaryPending}</p>
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
                  onRemove={agendaOpen ? () => setAskRemoveAgenda(agenda) : null}
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
                                  <Button size="sm" onClick={() => setPromoting(todo)} type="button">
                                    {meetingScreen.promote}
                                  </Button>
                                )}
                                {/* E73 후보 삭제 — 확인을 묻지 않는다. 아직 업무가 아니다 */}
                                <IconButton name="close" size={14} label={meetingScreen.dropTodo} onClick={() => void run(() => removeMeetingTodo(meeting.meeting_id, todo.todo_id))} style={{ width: 30, height: 30, color: "var(--scax-color-ink-assistive)" }} />
                              </>
                            ) : null,
                          }))
                      : null
                  }
                />
              ))
            )}

            {/* E35 [안건 추가] — 회의 전에는 늘, 완료·실패에서는 회의록을 고치는 동안 선다.
                자리는 시안대로 «안건 목록 바로 아래» 다. 계약은 그대로 `addMeetingAgenda` 하나다. */}
            {agendaOpen && (
              <div className="scax-add-agenda">
                <input
                  aria-label={meetingScreen.agendaPlaceholder}
                  className="scax-add-agenda__input"
                  onChange={(event) => setAgendaDraft(event.target.value)}
                  placeholder={meetingScreen.agendaPlaceholder}
                  type="text"
                  value={agendaDraft}
                />
                <Button size="sm" disabled={agendas.length>= 20 || agendaDraft.trim().length === 0 || busy}
                  onClick={() =>
                    void run(async () => {
                      await addMeetingAgenda(meeting.meeting_id, agendaDraft.trim());
                      setAgendaDraft("");
                    })
                  }
                  type="button"
                >
                  {meetingScreen.addAgenda}
                </Button>
              </div>
            )}
          </div>

          {/* E80·E81 — 「진행 중」에만, 쓰는 사람은 회의를 만든 사람 하나다 (SPEC §6-1).
              그 밖의 참석자는 같은 목록을 읽기로 본다 */}
          {/* 업스트림 자리를 가진 창이 따로 있으면 여기서는 메모도 올리지 않는다 — 마이크와 같은 규칙이다 */}
          {live && hosting && (
            <footer className="scax-note__composer">
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
            </footer>
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
        <CreateWorkModal
          assigneeCandidates={orderedAssignees}
          /* §8-B 14: 이 자리는 요청 전용이라 필드가 적다 — 시안대로 560 이다 */
          size="md"
          /* 회의에서 나온 요청이다 — 요청자는 시스템(회의)이고 상태는 언제나 「판단 대기」라
             그 두 줄을 폼에 두지 않는다 (§9-5 D40 · R-48). 보내는 값은 그대로다. */
          origin="meeting"
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
              <Button variant="text" onClick={() => setAskRemoveAgenda(null)} type="button">
                {meetingScreen.keep}
              </Button>
              <Button variant="solid" tone="danger" disabled={busy} onClick={() => {
                  const target = askRemoveAgenda;
                  setAskRemoveAgenda(null);
                  void run(() => removeMeetingAgenda(meeting.meeting_id, target.agenda_id), meetingScreen.agendaRemoved);
                }}
                type="button"
              >
                {meetingScreen.remove}
              </Button>
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
              <Button variant="text" onClick={() => setAskDropMaterial(null)} type="button">
                {meetingScreen.keep}
              </Button>
              <Button variant="solid" tone="danger" disabled={busy} onClick={() => {
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
              </Button>
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
              <Button variant="text" onClick={() => setAskLeave(null)} type="button">
                {meetingScreen.keep}
              </Button>
              <Button onClick={() => {
                  const proceed = askLeave;
                  setAskLeave(null);
                  setEditing(false);
                  proceed();
                }}
                type="button"
              >
                {meetingScreen.leave}
              </Button>
            </footer>
          </section>
        </div>
      )}
    </section>
  );
}

