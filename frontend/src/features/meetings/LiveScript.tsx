import { useEffect, useRef } from "react";

import { meetingElapsed, meetingScreen } from "../../lib/labels";

/**
 * `E26` 「스크립트」 탭의 실시간 원문.
 *
 * 확정 발화는 **추가 렌더**, 잠정 발화는 **교체 렌더**(회색)다 (SPEC §5.3). 회의 중 남긴 메모도 같은 시각
 * 축에 서고 「메모 · 적은 사람」으로 발화와 갈린다 (§5.4-8). 화자는 익명 라벨뿐이다 — 이름을 다는 조작은
 * 데모 범위 밖이다 (§5.4-5). 톤·강도를 두지 않는다.
 *
 * ── 이번 바퀴: 줄이 «쌓이는» 모양으로 갔다 ──
 * 예전에는 공용 `GutterList`(시각 | 화자 | 본문 세 칸 가로)를 썼다. 이 레일은 342 이고 앞 두 칸이
 * 120 을 먼저 가져가므로 본문에 남는 폭이 200 이 안 됐다 — 두세 낱말마다 줄이 바뀌어 말이 토막났다
 * (현재 화면 17). 폭을 못 늘리는 자리에서 **글자를 줄이는 대신 칸을 없앴다**:
 *
 *     00:15 · 화자 3          ← 작은 글씨 한 줄 (시각은 보조색, 화자는 medium)
 *     이걸 보통 링키가 하고, 그리고 각 카카오톡 알림 쪽 계약 진행이고요.   ← 열 전체 폭 본문
 *
 * 크기는 DS 램프 그대로다 — 본문은 label2(일반 굵기·본문색·행간 1.62), 메타는 caption1.
 * **폭 때문에 글자를 축소하지 않았다.**
 *
 * 지키는 것들은 그대로다: 근거 칩이 가리킨 줄의 강조(`active`)와 그 자리로 스크롤,
 * 잠정 줄의 흐린 색(`tentative`), 메모/발화 구분, 실시간 추가.
 */
export type ScriptRow = {
  key: string;
  kind: "talk" | "memo";
  who: string;
  atMs: number;
  /** 그 발화가 끝난 자리 — 근거 구간과 «겹치는지» 를 이것까지 보고 가른다 (D50). 잠정 줄은 시작과 같다. */
  endMs: number;
  text: string;
  /** 잠정 발화 — 회색으로 서고 저장되지 않는다. */
  tentative?: boolean;
  /** 근거 칩이 가리킨 구간 안에 있는 줄 (I05). */
  active?: boolean;
};

export function LiveScript({ rows }: { rows: ScriptRow[] }) {
  const marked = useRef<HTMLLIElement | null>(null);
  // 다른 자리에서 이 목록의 한 줄을 가리켰다 — 그 줄이 눈에 들어와야 가리킨 값이 있다 (I05).
  const activeKey = rows.find((row) => row.active)?.key ?? null;
  useEffect(() => {
    // jsdom 처럼 스크롤이 없는 환경도 있다 — 없으면 켜지기만 하고 넘어간다.
    if (activeKey) marked.current?.scrollIntoView?.({ block: "center" });
  }, [activeKey]);

  return (
    <ul aria-label={meetingScreen.tabScript} className="scax-script-list">
      {rows.map((row) => {
        const classes = ["scax-script-line"];
        if (row.kind === "memo") classes.push("scax-script-line--memo");
        if (row.tentative) classes.push("scax-script-line--tentative");
        if (row.active) classes.push("scax-script-line--active");
        return (
          <li className={classes.join(" ")} key={row.key} ref={row.key === activeKey ? marked : undefined}>
            <p className="scax-script-line__meta">
              {/* 시각은 회의 시작에서의 경과다 (D50) */}
              <span className="scax-script-line__at tabular">{meetingElapsed(row.atMs)}</span>
              <span aria-hidden className="scax-script-line__dot">·</span>
              <span className="scax-script-line__who">{row.who}</span>
            </p>
            <p className="scax-script-line__text">{row.text}</p>
          </li>
        );
      })}
    </ul>
  );
}
