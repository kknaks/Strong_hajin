import { calendarScreen } from "../../lib/labels";
import type { DateEdge } from "./calendarWrites";
import type { CalendarSegment } from "./calendarModel";
import { dayReach } from "./calendarModel";

/**
 * 격자 위의 일정 조각 (G-CAL-05).
 *
 * **색은 유형 둘로만 칠한다** — `--task` · `--meeting`. 상태는 여기 오지 않는다(SPEC §2.6).
 * 여러 날짜에 걸치는 것은 칸 여백을 넘어 이어 붙는 **띠**(`--bar`)이고, 머리·꼬리 칸만 모서리를 둥글린다.
 * 글자는 **띠의 머리 칸이나 그 주의 첫 칸에서 한 번만** 쓴다 — 칸마다 쓰면 같은 제목이 이레 내내 반복된다.
 *
 * **손잡이는 업무 띠에만 붙는다** (FE-2 · §E·§F) — 시간 배정 칩과 회의 띠에는 붙지 않는다.
 * 손잡이의 **정체는 화면의 좌우가 아니라 «필드»** 다(WARN-A): `start` 는 언제나 시작일을 정한다.
 * 뒤집힌 업무에서는 띠 위의 좌우가 바뀌어 보일 수 있다.
 */
export function EventBar({
  segment,
  date,
  weekFirst,
  weekLast,
  onGrab,
}: {
  segment: CalendarSegment;
  date: string;
  weekFirst: string;
  weekLast: string;
  /** 주면 손잡이가 선다. 잡는 순간 알리고, **보내는 것은 놓을 때**다 — 낙관적 잠금이라 끌 때마다 부르면 회차가 어긋난다. */
  onGrab?: (taskId: string, edge: DateEdge) => void;
}) {
  const head = date === segment.from;
  const multi = segment.to > segment.from;
  const tail = date === segment.to;
  // 손잡이는 «날짜 띠»의 것이다 — 시간이 붙은 칩(배정)과 회의에는 달지 않는다.
  const resizable = Boolean(onGrab) && segment.kind === "task" && segment.time === null && segment.taskId !== null;
  const classes = multi
    ? ["scax-event", "scax-event--bar", `scax-event--${segment.kind}`, head ? "scax-event--bar-head" : "", tail ? "scax-event--bar-tail" : ""]
    : ["scax-event", `scax-event--${segment.kind}`];
  if (resizable) classes.push("scax-event--resizable");
  /* 띠의 글자는 «그 주에 닿는 칸 수»만큼 옆으로 흘러야 한 번 쓴 제목이 띠 전체에 걸린다.
     12px 은 칸의 좌우 여백 둘(`--scax-space-150` × 2)로, 시안 `calendar.v1.jsx:131` 의 값 그대로다. */
  const reach = dayReach(segment, weekFirst, weekLast);
  const spread = multi ? { width: `calc(${reach * 100}% - ${reach * 12}px)` } : undefined;
  return (
    <span className={classes.filter(Boolean).join(" ")}>
      {head || date === weekFirst ? (
        <span className="scax-event__span-label" style={spread}>
          {segment.time ? `${segment.time} ` : ""}
          {segment.title}
        </span>
      ) : null}
      {resizable && head ? <EventHandle edge="start" onGrab={onGrab!} taskId={segment.taskId!} /> : null}
      {resizable && tail ? <EventHandle edge="end" onGrab={onGrab!} taskId={segment.taskId!} /> : null}
    </span>
  );
}

/**
 * 띠의 끝을 잡는 자리.
 *
 * 칸 전체가 단추이므로 **눌림이 위로 새지 않게** 막는다 — 손잡이를 잡은 것이 그 날을 고른 것이 되면
 * 레일이 같이 좁혀진다.
 */
export function EventHandle({
  taskId,
  edge,
  onGrab,
}: {
  taskId: string;
  edge: DateEdge;
  onGrab: (taskId: string, edge: DateEdge) => void;
}) {
  return (
    <span
      className={`scax-event__handle scax-event__handle--${edge}`}
      onClick={(event) => event.stopPropagation()}
      onPointerDown={(event) => {
        event.stopPropagation();
        event.preventDefault();
        onGrab(taskId, edge);
      }}
      role="presentation"
      title={edge === "start" ? calendarScreen.grabStart : calendarScreen.grabEnd}
    />
  );
}

/** 그 줄에 아무것도 없는 칸 — 자리를 비워 두면 아래 줄이 올라와 띠가 층을 바꾼다. */
export function EventGhost() {
  return <span aria-hidden className="scax-event scax-event--ghost" />;
}

/** 줄 상한을 넘긴 만큼. 「더보기」 단추는 이 판의 범위 밖이라 **읽는 표시**로만 선다. */
export function EventMore({ count }: { count: number }) {
  return <span className="scax-event scax-event--more">{calendarScreen.more(count)}</span>;
}
