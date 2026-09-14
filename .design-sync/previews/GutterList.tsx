import { GutterList, TimeChip } from "ax-workspace-frontend";

export default { title: "General/GutterList", component: GutterList };

/**
 * 왼쪽에 고정폭 메타 칸을 두고 오른쪽에 본문을 흘리는 목록 (`DS-gaps` G-26).
 * 「같은 시각 축에 종류가 다른 줄이 섞여 서는」 자리 — 원문 · 활동 기록.
 */
export const Transcript = () => (
  <div className="surface-card" style={{ width: 640 }}>
    <GutterList
      label="원문 스크립트"
      rows={[
        { key: "1", gutter: <TimeChip label="00:12" />, aside: "김도현", body: "이번 주 회의록 템플릿부터 정리하고 넘어가겠습니다." },
        { key: "2", gutter: <TimeChip label="00:38" />, aside: "박서진", body: "지난번 액션 아이템 중 두 건이 아직 안 닫혔어요." },
        { key: "3", gutter: <TimeChip label="01:05" />, aside: "이하늘", body: "회의실 예약은 THE CONNECT 쪽과 붙여 두는 게 낫겠습니다.", active: true },
        { key: "4", gutter: <TimeChip label="01:44" />, aside: "김도현", body: "그럼 그 건은 제가 맡고, 마감은 다음 주 금요일로 두겠습니다." },
        { key: "5", gutter: <TimeChip label="02:10" />, aside: "정민우", body: "전사 품질이 낮은 구간이 있어서 2-pass 재전사를 돌려 두었습니다.", muted: true },
      ]}
    />
  </div>
);

/** 둘째 칸(`aside`) 없이 — 없으면 칸 자체가 서지 않는다 */
export const Activity = () => (
  <div className="surface-card" style={{ width: 560 }}>
    <GutterList
      gutterWidth={92}
      label="활동 기록"
      rows={[
        { key: "a", gutter: "09-13 14:02", body: "김도현 님이 마감일을 2026-09-20 으로 정했습니다." },
        { key: "b", gutter: "09-13 11:40", body: "박서진 님이 상태를 진행 중으로 바꿨습니다." },
        { key: "c", gutter: "09-12 17:25", body: "담당자가 김도현 님으로 바뀌었습니다." },
        { key: "d", gutter: "지금", body: "저장하는 중…", muted: true },
      ]}
    />
  </div>
);
