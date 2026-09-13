import { GutterList } from "../../ds/GutterList";
import { meetingElapsed, meetingScreen } from "../../lib/labels";

/**
 * `E26` 「스크립트」 탭의 실시간 원문.
 *
 * 확정 발화는 **추가 렌더**, 잠정 발화는 **교체 렌더**(회색)다 (SPEC §5.3). 회의 중 남긴 메모도 같은 시각
 * 축에 서고 「메모 · 적은 사람」으로 발화와 갈린다 (§5.4-8). 화자는 익명 라벨뿐이다 — 이름을 다는 조작은
 * 데모 범위 밖이다 (§5.4-5). 톤·강도를 두지 않는다.
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
  return (
    <GutterList
      asideWidth={64}
      gutterWidth={56}
      label={meetingScreen.tabScript}
      rows={rows.map((row) => ({
        key: row.key,
        muted: row.tentative,
        active: row.active,
        /* 한 줄에 세 칸 — 시각 · 화자 · 내용. 시각은 회의 시작에서의 경과다 (D50) */
        gutter: <span className="t-meta tabular">{meetingElapsed(row.atMs)}</span>,
        aside: <span className="gutter-speaker">{row.who}</span>,
        body: row.text,
      }))}
    />
  );
}
