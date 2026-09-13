import { useCallback, useEffect, useRef, useState } from "react";
import type React from "react";

import { Empty } from "../../ds/Empty";
import { meetingScreen } from "../../lib/labels";
import { MeetingDetailPage } from "./MeetingDetailPage";
import { MeetingListPage } from "./MeetingListPage";

/**
 * 회의 — **한 화면 4칸** (바퀴 6a).
 *
 * 시안(`workspace.v1.jsx:1`)이 직접 말한다: 「회의 한 화면(4칸) … 목록과 상세를 페이지로 나누지 않는다」.
 * 그래서 `openMeetingId` 로 두 화면을 갈아 끼우던 라우팅 분기를 없애고, 그것을
 * **「목록 칸의 선택 상태」**로 내렸다 (M-1). `surface="meetings"` 하나가 네 칸을 그린다.
 *
 *   1 nav(180/65, 바퀴 2) · 2 목록 380 · 3 상세 min 640 · 4 첨부·스크립트 342
 *
 * ── 이탈 가드 (M-2) ──
 * 페이지 전환이 사라졌으니 **가드를 걸 자리도 옮겨야 한다.** 예전에는 breadcrumb 의 「회의 목록」을
 * 누를 때 물었고, 이제는 **다른 회의를 고르는 순간**에 같은 것을 묻는다. 고치던 것이 있는데
 * 묻지 않고 선택이 바뀌면 쓰던 내용이 조용히 사라진다 — 그것이 이 바퀴에서 가장 깨지기 쉬운 곳이다.
 *
 * ── 접는 칸 (M-5) ──
 * 시안은 `focus` 일 때 **목록 칸**이 사라진다(nav|상세|첨부). 첨부 칸은 늘 선다.
 * 예전 우리 코드는 반대로 첨부 칸을 접었다 — 뒤집었다.
 *
 * 칸 «안» 의 모양(카드·배지·회의록 머리 …)은 이 바퀴가 손대지 않는다 — 6b·6c 몫이다.
 */
export function MeetingWorkspace({
  onError,
  onNotice,
  onRegisterRefresh,
  onRegisterHeaderActions,
  onRegisterRails,
  onSessionLost,
  canCreateWorkRequests,
  ownerName,
  focusMeetingId,
  onFocusHandled,
}: {
  onError: (message: string | null) => void;
  onNotice: (message: string) => void;
  onRegisterRefresh?: (refresh: (() => Promise<void>) | null) => void;
  onRegisterHeaderActions?: (actions: React.ReactNode) => void;
  onRegisterRails?: (rails: { left?: React.ReactNode; right?: React.ReactNode }) => void;
  onSessionLost: () => void;
  canCreateWorkRequests: boolean;
  ownerName: string;
  focusMeetingId?: string | null;
  onFocusHandled?: () => void;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  /** 시안의 `focus` — 켜면 목록 칸이 사라지고 상세가 넓어진다 (M-5). */
  const [focus, setFocus] = useState(false);
  /* 첨부·스크립트 칸: 여기서 «빈 칸» 만 내주고 상세가 그 안에 포털로 그린다.
     노드를 위로 올리면 렌더마다 새 노드라 등록이 끝없이 돈다 — 그래서 자리만 넘긴다. */
  const [sideRailHost, setSideRailHost] = useState<HTMLElement | null>(null);
  const sideRail = <div ref={setSideRailHost} style={{ display: "contents" }} />;

  /*
   * M-2: 고치던 것이 있으면 «선택을 바꾸기 전에» 상세가 한 번 묻는다.
   * 상세가 `onRegisterLeaveGuard` 로 올려 준 가드를 여기서 부른다 — 예전에 breadcrumb 이 하던 일이다.
   */
  const leaveGuard = useRef<((proceed: () => void) => void) | null>(null);
  const registerLeaveGuard = useCallback((guard: ((proceed: () => void) => void) | null) => {
    leaveGuard.current = guard;
  }, []);
  /* 지금 무엇을 고르고 있는지는 ref 로도 들고 있다 — 고르는 함수가 매번 새로 만들어지면
     목록 칸이 통째로 다시 그려지고, setState 업데이터 «안» 에서 또 setState 를 부르면 안 되기 때문이다. */
  const selectedRef = useRef<string | null>(null);
  const selectMeeting = useCallback((meetingId: string) => {
    const next = meetingId || null;
    if (selectedRef.current === next) return;
    const proceed = () => {
      selectedRef.current = next;
      setSelected(next);
    };
    if (leaveGuard.current) leaveGuard.current(proceed);
    else proceed();
  }, []);

  const listRail = (
    <MeetingListPage
      focusMeetingId={focusMeetingId}
      onError={onError}
      onFocusHandled={onFocusHandled}
      onNotice={onNotice}
      onOpenMeeting={selectMeeting}
      onRegisterHeaderActions={onRegisterHeaderActions}
      onRegisterRefresh={onRegisterRefresh}
      selected={selected}
    />
  );

  /* deps 는 «칸을 이루는 값» 이다. 만들어진 노드(listRail)를 deps 로 삼으면 렌더마다 새 것이라
     등록 → 셸 setState → 재렌더 → 등록이 끝없이 돈다. */
  useEffect(() => {
    if (!onRegisterRails) return;
    // focus 면 목록 칸이 사라진다. 첨부 칸은 늘 선다 — 고른 회의가 없으면 그 칸이 스스로 빈 상태를 낸다.
    onRegisterRails({ left: focus ? undefined : listRail, right: sideRail });
    return () => onRegisterRails({});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus, focusMeetingId, onRegisterRails, selected]);

  return selected ? (
    <MeetingDetailPage
      canCreateWorkRequests={canCreateWorkRequests}
      focus={focus}
      key={selected}
      meetingId={selected}
      onBack={() => selectMeeting("")}
      onError={onError}
      onNotice={onNotice}
      onOpenMeeting={selectMeeting}
      onRegisterLeaveGuard={registerLeaveGuard}
      onRegisterRefresh={onRegisterRefresh}
      sideRailHost={sideRailHost}
      onSessionLost={onSessionLost}
      onToggleFocus={() => setFocus((value) => !value)}
      onTitleChange={() => undefined}
      ownerName={ownerName}
    />
  ) : (
    /* M-7: 시안에는 「아무것도 안 고른」 상태가 없다. 가짜 화면을 지어내지 않고, 무엇을 하면 되는지만 한 줄로 말한다. */
    <div className="meeting-columns detail single">
      <Empty title={meetingScreen.panelEmpty} />
    </div>
  );
}
