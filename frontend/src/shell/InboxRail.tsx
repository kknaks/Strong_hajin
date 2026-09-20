import { useState } from "react";
import { SegmentedControl } from "../ds/SegmentedControl";
import { canMarkRead, type RequestInboxItem } from "../features/work/requestInbox";
import { Icon } from "../ds/icons/Icon";
import { Badge } from "../ds/Badge";
import { Button } from "../ds/Button";
import { Empty } from "../ds/Empty";
import { Skeleton } from "../ds/Skeleton";
import { StatusNote } from "../ds/StatusNote";
import { formatDate, personName, workRequestStateLabel } from "../lib/labels";
import type { Persona } from "../lib/viewModels";

export type InboxState = "loading" | "error" | "ready";

/** 업무 요청과 CC 요청만 담는다. TaskReference와 AX 판단 항목은 이 목록의 원천이 아니다. */
export function InboxRail({ items, state, personas, personaId = "", onOpen, onRetry, onAccept, onReject, onRead, reading = [], canDecide = false, busy = false }: {
  items: RequestInboxItem[];
  state: InboxState;
  personas: Persona[];
  /** 지금 보는 사람 — `[읽음]` 을 그릴지 판정하는 데만 쓴다. 권한은 서버가 다시 본다. */
  personaId?: string;
  onOpen: (item: RequestInboxItem) => void;
  onRetry: () => void;
  canDecide?: boolean;
  busy?: boolean;
  onAccept?: (item: RequestInboxItem) => void;
  onReject?: (item: RequestInboxItem) => void;
  /**
   * 참고 항목을 읽음으로 접는다 (SPEC-001 U-12). 안 주면 단추를 그리지 않는다.
   * **카드를 여는 것도 같은 명령을 부른다** — 조회가 스스로 읽음을 만드는 것이 아니라, 화면이
   * 「열었다」는 사람의 행동을 읽음으로 번역해 명시적으로 부른다.
   */
  onRead?: (item: RequestInboxItem) => void;
  /** 지금 읽는 중인 항목들 — 연타가 두 번째 명령이 되지 않게 그 단추를 잠근다. */
  reading?: string[];
}) {
  const [filter, setFilter] = useState<"all" | "work" | "reference">("all");
  const shown = filter === "all" ? items : items.filter((item) => item.category === filter);
  /* 여는 것도 읽는 것이다 — 두 CTA 가 같은 한 명령을 부른다(U-12). 여는 일은 그대로 일어난다:
     읽음이 실패해도 상세는 열린다. 「못 읽었으니 못 연다」는 이 화면의 말이 아니다. */
  const open = (item: RequestInboxItem) => {
    onOpen(item);
    if (onRead && canMarkRead(item, personaId)) onRead(item);
  };
  let body;
  if (state === "loading") {
    body = <Skeleton label="수신함을 불러오는 중" rows={3} />;
  } else if (state === "error") {
    body = (
      <StatusNote tone="danger">
        수신함을 불러오지 못했습니다.{" "}
        <Button onClick={onRetry} size="sm" type="button" variant="text">다시 시도</Button>
      </StatusNote>
    );
  } else if (shown.length === 0) {
    body = <Empty description="나에게 온 업무 요청을 여기에서 확인하고 응답합니다." title="받은 업무 요청이 없습니다" />;
  } else {
    body = shown.map((item) => (
      <article className="scax-inbox-card openable" data-request-id={item.request_id} key={item.request_id} onClick={() => open(item)}>
        <div className="scax-inbox-card__content">
          <div className="scax-inbox-card__top">
            <Badge tone={item.category === "work" ? "accent" : "neutral"}>{item.category === "work" ? "업무 요청" : "참고"}</Badge>
            <Badge tone="neutral">{workRequestStateLabel[item.state]}</Badge>
          </div>
          <h3 className="scax-inbox-card__title"><button className="scax-inbox-card__open" type="button" onClick={(event) => { event.stopPropagation(); open(item); }}>{item.title}</button></h3>
          {item.description && <p className="scax-inbox-card__excerpt">{item.description}</p>}
          <div className="scax-inbox-card__meta">
            <span className="scax-inbox-card__meta-who">
              {item.requester_kind === "system" ? "회의" : personName(personas.find((person) => person.id === item.requester_id)?.display_name ?? "요청자")}
            </span>
            {item.due_date && <><span className="scax-inbox-card__meta-sep" /><span>기한 {formatDate(item.due_date)}</span></>}
          </div>
        </div>
        {/*
          * 참고 카드의 `[읽음]` — **참조자에게만** 그린다. 업무 요청 카드에는 세우지 않는다:
          * 그 갈래는 `[수락]`·`[거절]` 로 접히고, 읽음이 수락을 대신하지 않는다 (U-12).
          * 성공에 토스트를 띄우지 않는다 — 카드가 사라지는 것이 결과다.
          */}
        {onRead && canMarkRead(item, personaId) && (
          <div className="scax-button-group">
            <Button
              disabled={reading.includes(item.request_id)}
              onClick={(event) => { event.stopPropagation(); onRead(item); }}
              size="sm"
              tone="neutral"
              type="button"
              variant="outlined"
            >
              읽음
            </Button>
          </div>
        )}
        {item.category === "work" && canDecide && (item.state === "pending" || item.state === "negotiating") && (
          <div className="scax-button-group">
            <Button disabled={busy || !onAccept} onClick={(event) => { event.stopPropagation(); onAccept?.(item); }} size="sm" tone="primary" type="button" variant="outlined">수락</Button>
            <Button disabled={busy || !onReject} onClick={(event) => { event.stopPropagation(); onReject?.(item); }} size="sm" tone="neutral" type="button" variant="outlined">거절</Button>
          </div>
        )}
      </article>
    ));
  }
  return (
    <section aria-label="업무 요청 수신함" className="scax-gutter-list">
      <header className="scax-gutter-list__header">
        <h2 className="scax-gutter-list__title">
          <Icon name="inbox" size={24} />
          수신함
          {state === "ready" && items.length > 0 && <Badge variant="count">{items.length}</Badge>}
        </h2>
        <SegmentedControl ariaLabel="수신함 분류" value={filter} onChange={setFilter} options={[
          { value: "all", label: "전체" }, { value: "work", label: "업무" }, { value: "reference", label: "참고" },
        ]} />
      </header>
      <div className="scax-gutter-list__body">{body}</div>
    </section>
  );
}
