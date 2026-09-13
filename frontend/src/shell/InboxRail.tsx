import { useState } from "react";

import { Badge } from "../ds/Badge";
import { Button } from "../ds/Button";
import { Empty } from "../ds/Empty";
import { SegmentedControl } from "../ds/SegmentedControl";
import { Skeleton } from "../ds/Skeleton";
import { StatusNote } from "../ds/StatusNote";
import { emptyActionLabel, personName } from "../lib/labels";
import { type ActionItemEnvelope } from "../lib/viewModels";

/**
 * 좌 레일 — 수신함 (바퀴 5b).
 *
 * 시안의 `InboxRail` + `InboxCard` 자리에 **우리 「판단이 필요한 업무」(`actionItems`)** 를 그린다.
 * 바퀴 5a 까지 이것은 본문 맨 위의 접이식 패널이었고, 시안대로 레일로 «옮겨» 왔다 — 복제가 아니다.
 *
 * ── 어댑터가 버린 시안 필드 (우리 데이터에 없다 · K-1 「발명 금지」) ──
 * `avatar`(아바타 이미지) · `date`(수신 시각) · `source`/`sourceIcon`(어디서 왔나 + 외부 링크).
 * `ActionItemEnvelope` 에 대응하는 값이 하나도 없어서 그 자리를 아예 그리지 않는다.
 * 빈 원이나 「—」 를 두면 값이 있는데 비어 보이는 것과 구별되지 않는다.
 *
 * ── 시안에 없고 우리에게만 있는 것 ──
 * `status`(판단 대기 / 조정 필요) · `submission_version`(회차) · `waiting_on`(누구 차례).
 * 앞의 둘은 카드 머리·꼬리에 남기고, 분류 컨트롤도 이 `status` 축으로 세웠다 —
 * 시안의 `INBOX_FILTERS`(업무/알림)는 우리 데이터에 없는 축이라 그대로 옮길 수 없다.
 *
 * 카드를 누르면 지금처럼 판단 드로어가 열린다. 시안의 카드 안 단추 두 개(「확인완료」 등)는
 * 무엇이 허용되는지가 envelope 의 `allowed_commands` 에 있고 그 실행은 드로어가 맡는다 — 여기서 복제하지 않는다.
 */

export type InboxState = "loading" | "error" | "ready";

type InboxFilter = "all" | "awaiting_review" | "awaiting_revision";

const FILTERS: ReadonlyArray<{ value: InboxFilter; label: string }> = [
  { value: "all", label: "전체" },
  { value: "awaiting_review", label: "판단 대기" },
  { value: "awaiting_revision", label: "조정 필요" },
];

const STATUS_LABEL: Record<ActionItemEnvelope["status"], string> = {
  awaiting_review: "판단 대기",
  awaiting_revision: "조정 필요",
  resolved: "판단 완료",
};

export function InboxRail({
  items,
  state,
  onOpen,
  onRetry,
}: {
  items: ActionItemEnvelope[];
  state: InboxState;
  onOpen: (item: ActionItemEnvelope) => void;
  onRetry: () => void;
}) {
  const [filter, setFilter] = useState<InboxFilter>("all");
  const shown = filter === "all" ? items : items.filter((item) => item.status === filter);

  let body;
  if (state === "loading") {
    body = <Skeleton label="수신함을 불러오는 중" rows={3} />;
  } else if (state === "error") {
    body = (
      <StatusNote tone="danger">
        수신함을 불러오지 못했습니다.{" "}
        <Button onClick={onRetry} size="sm" type="button" variant="text">
          다시 시도
        </Button>
      </StatusNote>
    );
  } else if (shown.length === 0) {
    body =
      items.length === 0 ? (
        <Empty description="동료의 요청, 관리자의 배정, AX 제안이 오면 여기에 쌓입니다." title="판단할 항목이 없습니다" />
      ) : (
        <Empty
            actionLabel={emptyActionLabel.filter} onAction={() => setFilter("all")} title="이 분류에는 판단할 항목이 없습니다" variant="filter" />
      );
  } else {
    body = shown.map((item) => <InboxCard item={item} key={item.action_item_id} onOpen={onOpen} />);
  }

  return (
    <section aria-label="판단이 필요한 업무" className="scax-gutter-list">
      <header className="scax-gutter-list__header">
        <h2 className="scax-gutter-list__title">
          수신함
          {state === "ready" && items.length > 0 && <Badge variant="count">{items.length}</Badge>}
        </h2>
        <SegmentedControl ariaLabel="수신함 분류" onChange={setFilter} options={FILTERS} value={filter} />
      </header>
      <div className="scax-gutter-list__body">{body}</div>
    </section>
  );
}

function InboxCard({ item, onOpen }: { item: ActionItemEnvelope; onOpen: (item: ActionItemEnvelope) => void }) {
  return (
    <article
      className="scax-inbox-card openable"
      data-action-item-id={item.action_item_id}
      data-kind={item.kind}
      onClick={() => onOpen(item)}
    >
      <div className="scax-inbox-card__content">
        <div className="scax-inbox-card__top">
          <Badge tone="accent">{item.operation_label}</Badge>
          <Badge tone={item.status === "awaiting_revision" ? "danger" : "neutral"}>{STATUS_LABEL[item.status]}</Badge>
        </div>
        <h3 className="scax-inbox-card__title">{item.subject}</h3>
        <p className="scax-inbox-card__excerpt">{item.current_question}</p>
        {/* 시안의 아바타·수신 시각·출처 자리는 우리 데이터에 없어서 그리지 않는다 (K-1). */}
        <div className="scax-inbox-card__meta">
          <span className="scax-inbox-card__meta-who">
            {item.waiting_on ? `${personName(item.waiting_on.display_name)} 차례` : "차례 없음"}
          </span>
          <span className="scax-inbox-card__meta-sep" />
          <span>{item.submission_version > 1 ? `${item.submission_version}회차` : "첫 회차"}</span>
        </div>
      </div>
      <Button
        onClick={(event) => {
          event.stopPropagation();
          onOpen(item);
        }}
        size="sm"
        tone="primary"
        type="button"
        variant="solid"
      >
        판단하기
      </Button>
    </article>
  );
}
