import { useEffect, useRef, useState } from "react";

import { Button } from "../../ds/Button";
import { Select, type SelectOption } from "../../ds/Select";
import { readMeetingRooms } from "../../lib/api";
import { emptyActionLabel, meetingScreen, selectLabel } from "../../lib/labels";
import type { MeetingRoom } from "../../lib/viewModels";

/**
 * 회의실 셀렉트 — **부품 하나를 네 자리가 같이 쓴다**(SPEC-010 §2.2 · §5 프론트): 생성 모달 · 수정 모달 · AX 회의 생성 카드 · AX 회의 수정 카드.
 *
 * ```text
 * 기존 — 회의실 3 (변경 안 함)          ← 수정 때만 · 맨 위 (원래 방이 없던 회의면 이 줄도 없다)
 * ─────────────────────────
 * 회의실 예약 없음                       ← 생성 때는 기본값
 * 회의실 1 (6인)                        ← Connect 가용 목록 — 그 시간·그 인원에 쓸 수 있는 방만
 * ```
 *
 * - 목록은 날짜·시작·종료·참석 인원이 바뀔 때마다 **300ms 기다렸다** 다시 받는다(생성 모달의 틀 그대로)
 * - 기존 방을 새 조건에 쓸 수 없으면 그 줄은 비활성 + 이유. **선택은 「예약 없음」 으로 옮겨 가지 않는다**(OQ-1016) —
 *   부르는 쪽이 `onStatus` 의 `blocked` 를 보고 [저장]을 막고 그 옆에 이유를 한 줄로 보인다(H-2)
 * - 「가용 없음」(조회는 됨)과 「조회 실패」(503 등 — Connect 에 닿지 않음)를 가른다(OQ-906). 조회 실패면 기존 줄은 「(확인 못 함)」 으로 고를 수 있다
 * - 고른 방이 새 목록에서 빠지면 그 사실을 말한다 — 조용히 「예약 없음」 이 되지 않는다(WP1 검수 W-3)
 */

/** 셀렉트 값 — `"keep"`(기존 방 그대로 — 수정만) · `"none"`(회의실 예약 없음) · `"unset"`(아직 안 고름 — 거절 뒤) · 방 번호 글자. */
export type RoomChoice = "keep" | "none" | "unset" | `${number}`;

export type RoomQuery = { starts_at: string; ends_at: string; people: number };

export type RoomSelectStatus = {
  state: "loading" | "ok" | "failed";
  /** 고른 값으로는 저장할 수 없다 — 기존 방이 새 조건에 안 되거나, 고른 방이 목록에서 빠졌거나, 아직 안 골랐다. */
  blocked: boolean;
  /** `blocked` 의 이유 한 줄(부르는 쪽이 [저장] 옆에 낸다). */
  reason: string | null;
};

export function roomChoiceId(choice: RoomChoice): number | null {
  return choice === "keep" || choice === "none" || choice === "unset" ? null : Number(choice);
}

export function RoomSelect({
  query,
  meetingId = null,
  currentName = null,
  value,
  onChange,
  onStatus,
  refused = null,
  requestedName = null,
  name = "meeting-room",
  disabled = false,
}: {
  /** 시간·인원. 아직 정하지 못했으면 `null` — 부르지 않는다. */
  query: RoomQuery | null;
  /** 수정 중인 회의(수정 모달 · AX 수정 카드). 있으면 맨 위에 기존 줄이 선다. */
  meetingId?: string | null;
  /** 지금 방 이름 — 조회가 실패해도 기존 줄을 「(확인 못 함)」 으로 세우는 데 쓴다. 방이 없던 회의면 `null`. */
  currentName?: string | null;
  value: RoomChoice;
  onChange: (next: RoomChoice) => void;
  onStatus?: (status: RoomSelectStatus) => void;
  /** 저장이 409 로 거절됐을 때 서버가 준 「지금 가능한 방」 — 다음 조건 변경까지 이 목록으로 그린다. */
  refused?: MeetingRoom[] | null;
  /** 이 이름의 방을 미리 고른다(불러오기 · AX 제안). 그 방을 지금 쓸 수 없으면 이유를 한 줄로 말한다(W-3). */
  requestedName?: string | null;
  /** 라디오 묶음 이름 — 한 화면에 둘이 서도 섞이지 않게. */
  name?: string;
  disabled?: boolean;
}) {
  const editing = Boolean(meetingId);
  const [rooms, setRooms] = useState<MeetingRoom[]>([]);
  const [state, setState] = useState<RoomSelectStatus["state"]>("loading");
  const [attempt, setAttempt] = useState(0);
  const first = useRef(true);
  const key = query ? `${query.starts_at}|${query.ends_at}|${query.people}|${meetingId ?? ""}` : "";

  useEffect(() => {
    if (!query) return;
    let cancelled = false;
    const load = () => {
      setState("loading");
      /* `Promise.resolve` 로 감싸 «약속이 아닌 값» 도 같은 길로 받는다 */
      Promise.resolve()
        .then(() => readMeetingRooms({ ...query, meeting_id: meetingId }))
        .then((next) => {
          if (cancelled) return;
          setRooms(Array.isArray(next) ? next : []);
          setState("ok");
        })
        .catch(() => {
          /* 503 ROOM_SERVICE_UNAVAILABLE 를 포함해 받지 못한 것은 전부 「조회 실패」다 — 「가용 없음」(빈 목록)과 가른다 */
          if (cancelled) return;
          setRooms([]);
          setState("failed");
        });
    };
    if (first.current) {
      first.current = false;
      load();
      return () => {
        cancelled = true;
      };
    }
    const timer = window.setTimeout(load, 300);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 조건은 `key` 한 글자로 본다(객체가 렌더마다 새로 만들어진다)
  }, [key, attempt]);

  /* 거절 목록은 그 조건에서만 쓴다 — 조건이 바뀌면 다시 받은 목록이 이긴다 */
  const [refusedKey, setRefusedKey] = useState<string | null>(null);
  useEffect(() => {
    if (refused) setRefusedKey(key);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refused]);
  const useRefused = Boolean(refused) && refusedKey === key;
  const list = useRefused ? (refused ?? []).map((room) => ({ ...room, available: room.available ?? true })) : rooms;

  const current = editing ? list.find((room) => room.current) ?? null : null;
  const keepName = current?.name ?? (editing ? currentName : null);
  const showKeep = editing && Boolean(keepName);
  const keepUsable = state === "failed" || !current || current.available !== false;
  const keepReason = current && current.available === false ? meetingScreen.roomUnavailableReason[current.unavailable_reason ?? ""] ?? meetingScreen.roomUnavailableFallback : null;
  const options = list.filter((room) => !room.current && room.available !== false);
  const picked = roomChoiceId(value);
  const pickedRoom = picked === null ? null : list.find((room) => room.room_id === picked) ?? null;
  const pickedGone = picked !== null && state === "ok" && (!pickedRoom || pickedRoom.available === false || pickedRoom.current);

  /* 미리 고를 방(불러오기 · AX 제안) — 이름이 같은 가용 방을 한 번 고른다. 못 쓰면 고르지 않고 이유를 말한다(W-3) */
  const requestHandled = useRef<string | null>(null);
  useEffect(() => {
    if (!requestedName || state !== "ok" || requestHandled.current === `${requestedName}|${key}`) return;
    requestHandled.current = `${requestedName}|${key}`;
    const match = options.find((room) => room.name === requestedName);
    if (match && (value === "none" || value === "unset")) onChange(String(match.room_id) as RoomChoice);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [requestedName, state, key]);
  const requestedGone =
    Boolean(requestedName) && state === "ok" && !options.some((room) => room.name === requestedName) && !(showKeep && keepName === requestedName);

  const blockedReason =
    value === "keep" && showKeep && !keepUsable
      ? meetingScreen.roomPickAgain
      : pickedGone
        ? meetingScreen.roomPickedGone(pickedRoom?.name ?? String(picked))
        : value === "unset"
          ? meetingScreen.roomPickAgain
          : null;

  const status: RoomSelectStatus = { state, blocked: Boolean(blockedReason), reason: blockedReason };
  const statusKey = `${status.state}|${status.blocked}|${status.reason ?? ""}`;
  useEffect(() => {
    onStatus?.(status);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusKey]);

  /*
   * 모양 = **드롭다운 셀렉트**(DS `Select` · 2루프 E-3 — 사용자 결정 SH-IMP-003 「셀렉트」). 목록 줄은 SPEC-010 §2.2 그대로:
   * 수정 때만 맨 위 「기존 — 회의실 N (변경 안 함)」(쓸 수 없으면 비활성 + 그 아래 이유 한 줄) → 구분(묶음 머리) → 「회의실 예약 없음」 → 가능한 방.
   * 생성 때는 첫 줄이 없다. 값 `"unset"`(거절 뒤 아직 안 고름)은 어느 줄도 아니라 트리거에 안내 글자가 선다.
   */
  const group = showKeep ? meetingScreen.roomGroup : undefined;
  const selectOptions: SelectOption[] = [
    ...(showKeep
      ? [
          {
            value: "keep",
            label: state === "failed" ? meetingScreen.roomKeepUnchecked(keepName!) : meetingScreen.roomKeep(keepName!),
            disabled: !keepUsable,
            description: keepUsable ? undefined : keepReason ?? undefined,
          },
        ]
      : []),
    { value: "none", label: meetingScreen.noRoom, group },
    ...options.map((room) => ({ value: String(room.room_id), label: room.name, group })),
  ];

  return (
    <div aria-busy={state === "loading"} className="meeting-room-select" data-room-select={name}>
      <Select
        disabled={disabled}
        emptyActionLabel={emptyActionLabel.filter}
        id={name}
        label={meetingScreen.place}
        labels={selectLabel}
        onChange={(next) => onChange(next as RoomChoice)}
        options={selectOptions}
        placeholder={meetingScreen.roomPickPlaceholder}
        value={value === "unset" ? "" : value}
      />
      {/* 기존 방을 고른 채인데 새 조건에 못 쓴다 — 목록을 열지 않아도 이유가 보이게(목록 안 회색 글자만으로 두지 않는다 · H-2) */}
      {value === "keep" && showKeep && !keepUsable && keepReason ? <p className="meeting-room-note meeting-room-note--warn">{keepReason}</p> : null}
      {state === "ok" && options.length === 0 && <p className="meeting-room-note">{meetingScreen.roomsEmpty}</p>}
      {state === "failed" && (
        <p className="meeting-room-note meeting-room-note--failed" role="alert">
          {meetingScreen.roomsFailed}
          <Button disabled={disabled} onClick={() => setAttempt((count) => count + 1)} size="sm" type="button">
            {meetingScreen.roomsRetry}
          </Button>
        </p>
      )}
      {pickedGone && <p className="meeting-room-note meeting-room-note--warn">{meetingScreen.roomPickedGone(pickedRoom?.name ?? String(picked))}</p>}
      {requestedGone && requestedName && <p className="meeting-room-note meeting-room-note--warn">{meetingScreen.roomCarriedGone(requestedName)}</p>}
    </div>
  );
}
