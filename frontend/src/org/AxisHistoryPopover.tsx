import { useEffect, useState } from "react";

import { ApiError, getOrganizationMemberHistory, type MemberHistoryAxis, type MemberHistoryEntry } from "../api";
import { Empty } from "../Empty";
import { Popover } from "../Popover";
import { Skeleton } from "../Skeleton";
import { formatPeriod, orgScreen } from "../labels";

/** 이력을 부르는 동안·부르고 나서. 못 읽은 것과 볼 자격이 없는 것은 다른 상태다. */
type HistoryView = { status: "loading" } | { status: "ready"; rows: MemberHistoryEntry[] } | { status: "error"; message: string };

/**
 * 한 축이 지나온 기간들. Popover 가 열릴 때만 그려지므로, 이 컴포넌트가 마운트되는 순간이 곧 「열렸다」다.
 *
 * 서버는 지금 값도 이력의 한 행으로 준다 — 아직 끝나지 않은 기간이다. 최신순도 서버가 정한다.
 */
function AxisHistoryList({ memberId, axis, onDenied }: { memberId: string; axis: MemberHistoryAxis; onDenied: (message: string) => void }) {
  const [view, setView] = useState<HistoryView>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    setView({ status: "loading" });
    void getOrganizationMemberHistory(memberId, axis)
      .then((rows) => {
        if (!cancelled) setView({ status: "ready", rows });
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        const denied = error instanceof ApiError && error.status === 403;
        const message = denied ? orgScreen.historyDenied : error instanceof Error ? error.message : orgScreen.historyDenied;
        setView({ status: "error", message });
        // 자격이 없다는 답은 이 작은 패널보다 화면 전체가 말해야 한다.
        if (denied) onDenied(message);
      });
    return () => {
      cancelled = true;
    };
  }, [axis, memberId, onDenied]);

  // Popover 는 열릴 때 한 번 재고 resize 마다 다시 잰다. 내용이 뒤늦게 도착하면 그 사이 높이가 달라지므로,
  // 도착한 뒤 한 번 더 재게 한다 — 그래야 위로 열지 아래로 열지가 실제 내용으로 정해진다.
  useEffect(() => {
    if (view.status === "loading") return;
    window.dispatchEvent(new Event("resize"));
  }, [view.status]);

  if (view.status === "loading") return <Skeleton label={orgScreen.historyLoading} rows={3} />;
  if (view.status === "error") return <Empty title={view.message} variant="error" />;
  if (view.rows.length === 0) return <Empty title={orgScreen.historyEmpty} variant="filter" />;
  return (
    <div style={{ display: "flex", flexDirection: "column" }}>
      {view.rows.map((row, index) => (
        <div
          key={`${row.value ?? ""}-${row.valid_from}-${index}`}
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 2,
            padding: "8px 10px",
            borderTop: index === 0 ? undefined : "1px solid var(--border-subtle)",
          }}
        >
          <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
            <span className="t-item" style={{ flex: 1, minWidth: 0 }}>
              {row.value ?? "—"}
              {row.unit_name && row.unit_name !== row.value ? ` · ${row.unit_name}` : ""}
            </span>
            <span className="t-meta tabular" style={{ flex: "none", fontSize: 12 }}>
              {formatPeriod(row.valid_from, row.valid_until)}
            </span>
          </div>
          <div style={{ display: "flex", alignItems: "baseline", gap: 8, fontSize: 12, color: "var(--text-secondary)" }}>
            <span style={{ flex: 1, minWidth: 0 }}>{row.reason ?? orgScreen.historyNoReason}</span>
            {row.actor && (
              <span className="t-meta" style={{ flex: "none", fontSize: 12 }}>
                {row.actor}
              </span>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

/**
 * 축 행 오른쪽의 「이력」 — 누르면 그 축이 지나온 기간을 팝오버로 편다 (v2 `14`: 고르기·보기는 팝오버).
 *
 * `axis` 가 없는 행(계층)은 부를 곳이 없으므로 눌리지 않는다. 볼 자격이 없는 사람에게는 아예 그리지 않는다 —
 * 눌러 봐야 403 인 버튼을 두면 없는 기능을 있는 것처럼 보이게 한다 (SPEC-005 §5).
 */
export function AxisHistoryButton({
  memberId,
  axis,
  title,
  onDenied,
}: {
  memberId: string;
  /** null 이면 이 축에는 이력 API 가 없다는 뜻이다. */
  axis: MemberHistoryAxis | null;
  title: string;
  onDenied: (message: string) => void;
}) {
  if (!axis) {
    return (
      <button className="btn ghost h30" disabled title={orgScreen.historyNotAnAxis} type="button">
        {orgScreen.history}
      </button>
    );
  }
  return (
    <Popover
      label={title}
      trigger={({ props }) => (
        <button className="btn ghost h30" {...props}>
          {orgScreen.history}
        </button>
      )}
      // v2 14 의 200–400 안에서 가장 좁은 패널(1440 에서 상세 패널 400)에 들어가는 폭. 400 이면 그 폭에서 잘린다.
      width={320}
    >
      {() => (
        <>
          <div className="popover-head" style={{ display: "flex", alignItems: "center", padding: "2px 10px 8px" }}>
            <b className="t-item">{title}</b>
          </div>
          <AxisHistoryList axis={axis} memberId={memberId} onDenied={onDenied} />
        </>
      )}
    </Popover>
  );
}
