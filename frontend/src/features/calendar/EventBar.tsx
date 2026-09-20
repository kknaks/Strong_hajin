import { calendarScreen } from "../../lib/labels";
import type { CalendarSegment } from "./calendarModel";
import { dayReach } from "./calendarModel";

/**
 * 격자 위의 일정 조각 (G-CAL-05 · FE-1 몫 — 띠와 `+N건 더`).
 *
 * **색은 유형 둘로만 칠한다** — `--task` · `--meeting`. 상태는 여기 오지 않는다(SPEC §2.6).
 * 여러 날짜에 걸치는 것은 칸 여백을 넘어 이어 붙는 **띠**(`--bar`)이고, 머리·꼬리 칸만 모서리를 둥글린다.
 * 글자는 **띠의 머리 칸이나 그 주의 첫 칸에서 한 번만** 쓴다 — 칸마다 쓰면 같은 제목이 이레 내내 반복된다.
 *
 * 손잡이(`--resizable`)·고스트·드래그는 **FE-2** 다. 이 부품은 읽기만 안다.
 */
export function EventBar({
  segment,
  date,
  weekFirst,
  weekLast,
}: {
  segment: CalendarSegment;
  date: string;
  weekFirst: string;
  weekLast: string;
}) {
  const head = date === segment.from;
  const multi = segment.to > segment.from;
  const tail = date === segment.to;
  const classes = multi
    ? ["scax-event", "scax-event--bar", `scax-event--${segment.kind}`, head ? "scax-event--bar-head" : "", tail ? "scax-event--bar-tail" : ""]
    : ["scax-event", `scax-event--${segment.kind}`];
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
    </span>
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
