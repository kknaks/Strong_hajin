import type { OrganizationActivityEvent } from "../api";
import { Empty } from "../Empty";
import { Skeleton } from "../Skeleton";
import { formatActivityTime, orgScreen } from "../labels";

/**
 * 하단 변경 기록 — 핸드오프 「변경 기록 (관리자만)」. `.plain-table`, 시각 120 · 변경 280 · 축 80 · 사유 · 기록자 120.
 *
 * 한 임명이 조직과 권한 두 줄로 남는 것이 정상이다 — 사건은 축마다 따로 기록되고, 이 표는 그것을 합치지 않는다.
 * 축 이름은 서버가 정한다(권한 · 소속 · 직책 · 조직). 실제 접근을 바꾸는 축은 권한 하나뿐이라 그것만 `badge ai` 다.
 */
export function ChangeLogPanel({
  events,
  failed,
  hasMore,
  loadingMore,
  onLoadMore,
}: {
  /** null 이면 아직 불러오는 중이다. */
  events: OrganizationActivityEvent[] | null;
  failed: boolean;
  hasMore: boolean;
  loadingMore: boolean;
  onLoadMore: () => void;
}) {
  const columns = orgScreen.changeLogColumns;
  return (
    <section aria-label={orgScreen.changeLogTitle} className="surface-card" style={{ marginTop: 24, padding: 0, overflow: "hidden" }}>
      <header
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 8,
          height: 44,
          padding: "0 24px",
          borderBottom: "1px solid var(--border-default)",
        }}
      >
        <h3 style={{ fontSize: 16 }}>{orgScreen.changeLogTitle}</h3>
      </header>
      {failed ? (
        <Empty title={orgScreen.changeLogError} variant="error" />
      ) : events === null ? (
        <div style={{ padding: 24 }}>
          <Skeleton label="변경 기록을 불러오는 중" rows={3} />
        </div>
      ) : events.length === 0 ? (
        <Empty title={orgScreen.changeLogEmpty} variant="filter" />
      ) : (
        <>
          <table className="plain-table">
            <thead>
              <tr>
                <th style={{ width: 120 }}>{columns.at}</th>
                <th style={{ width: 280 }}>{columns.summary}</th>
                <th style={{ width: 80 }}>{columns.axis}</th>
                <th>{columns.reason}</th>
                <th className="end" style={{ width: 120 }}>
                  {columns.actor}
                </th>
              </tr>
            </thead>
            <tbody>
              {events.map((event, index) => (
                <tr key={`${event.occurred_at}-${event.target_id}-${event.event_kind}-${index}`}>
                  <td className="tabular">{formatActivityTime(event.occurred_at)}</td>
                  <td className="title-cell">
                    {/* 맨 위 한 줄만 굵다 — 어디까지 읽었는지가 굵기로 보인다 (핸드오프). */}
                    {index === 0 ? <b className="t-item">{event.summary}</b> : event.summary}
                  </td>
                  <td>
                    <span className={event.axis === orgScreen.axes.access.label ? "badge ai" : "badge neutral"}>{event.axis}</span>
                  </td>
                  <td>{event.reason ?? orgScreen.historyNoReason}</td>
                  <td className="end">{event.actor_name}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {hasMore && (
            <div style={{ display: "flex", justifyContent: "center", padding: 12, borderTop: "1px solid var(--border-subtle)" }}>
              <button className="btn h30" disabled={loadingMore} onClick={onLoadMore} type="button">
                {orgScreen.changeLogMore}
              </button>
            </div>
          )}
        </>
      )}
    </section>
  );
}
