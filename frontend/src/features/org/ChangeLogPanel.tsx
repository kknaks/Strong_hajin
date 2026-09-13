import { Badge } from "../../ds/Badge";
import { DataTable, Td, Th } from "../../ds/DataTable";
import { Button } from "../../ds/Button";
import type { OrganizationActivityEvent } from "../../lib/api";
import { Empty } from "../../ds/Empty";
import { Skeleton } from "../../ds/Skeleton";
import { formatActivityTime, orgScreen } from "../../lib/labels";

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
    <section aria-label={orgScreen.changeLogTitle} className="org-change-log">
      <header className="org-change-log__head">
        <h3 className="org-panel__title">{orgScreen.changeLogTitle}</h3>
      </header>
      {failed ? (
        <Empty title={orgScreen.changeLogError} variant="error" />
      ) : events === null ? (
        <div className="org-panel__pad">
          <Skeleton label="변경 기록을 불러오는 중" rows={3} />
        </div>
      ) : events.length === 0 ? (
        <Empty title={orgScreen.changeLogEmpty} variant="filter" />
      ) : (
        <>
          <DataTable>
            <thead>
              <tr>
                <Th style={{ width: 120 }}>{columns.at}</Th>
                <Th style={{ width: 280 }}>{columns.summary}</Th>
                <Th style={{ width: 80 }}>{columns.axis}</Th>
                <Th>{columns.reason}</Th>
                <Th align="end" style={{ width: 120 }}>
                  {columns.actor}
                </Th>
              </tr>
            </thead>
            <tbody>
              {events.map((event, index) => (
                <tr key={`${event.occurred_at}-${event.target_id}-${event.event_kind}-${index}`}>
                  <Td className="tabular">{formatActivityTime(event.occurred_at)}</Td>
                  <Td title>
                    {/* 맨 위 한 줄만 굵다 — 어디까지 읽었는지가 굵기로 보인다 (핸드오프). */}
                    {index === 0 ? <b>{event.summary}</b> : event.summary}
                  </Td>
                  <Td>
                    <Badge tone={event.axis === orgScreen.axes.access.label ? "accent" : "neutral"}>{event.axis}</Badge>
                  </Td>
                  <Td>{event.reason ?? orgScreen.historyNoReason}</Td>
                  <Td align="end">{event.actor_name}</Td>
                </tr>
              ))}
            </tbody>
          </DataTable>
          {hasMore && (
            <div className="org-change-log__more">
              <Button size="sm" disabled={loadingMore} onClick={onLoadMore} type="button">
                {orgScreen.changeLogMore}
              </Button>
            </div>
          )}
        </>
      )}
    </section>
  );
}
