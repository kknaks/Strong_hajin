import type { ReactNode } from "react";

import { Button, IconButton } from "../../ds/Button";
import { Icon } from "../../ds/icons/Icon";
import { TimeChip } from "../../ds/TimeChip";
import { EmptyValue } from "../../ds/Empty";
import { meetingScreen } from "../../lib/labels";

/**
 * 안건 한 덩어리 (CMP-105) — 회의 상세의 회의록 칸이 쓴다.
 *
 * 본문은 문단이 아니라 **줄 목록**이다 (D12): 줄 하나에 짧은 문장 하나, 종류(논의·결정) 배지는 두지 않는다.
 * 고칠 때도 같은 모양이다 — 줄 하나가 한 줄 칸이고, 아래에 [내용 줄 추가]가 선다.
 *
 * **바퀴 6bc (§8-B 7·8)** — 시안은 이 덩어리를 카드로 감싸지 않는다. 흰 바닥에 좌우 24 여백을 주고
 * 아래 1px 선 하나로만 다음 덩어리와 가른다 — `meetings.css` 의 `.scax-agenda-block` 이 그 규칙이다
 * (「본문 칸은 업무 표와 같은 결이다」라고 그 파일이 스스로 적어 두었다). 그래서:
 *   · 구 `.meeting-agenda` + 인라인 `borderTop`/`padding` 을 벗었다. 첫 덩어리만 위 선이 없던 규칙은
 *     선이 **아래**로 내려가며 저절로 없어진다 — 자리에 따라 달라지던 인라인 분기가 사라졌다.
 *   · `compact`(목록 패널용 한 단 좁은 여백)를 지웠다. 그 패널은 바퀴 6a M-4 가 삭제했고,
 *     이 바퀴에서 남아 있던 죽은 `NotePanel` 까지 걷어내며 부르는 자리가 하나도 없어졌다.
 *   · 근거 시각 칩은 줄의 **오른쪽 끝**에 모인다 — `workspace.css` 의 `.scax-note-line__evidence`
 *     (`justify-content:flex-end`)를 쓴다. 바퀴 5 가 업무 화면에 실어 둔 그 규칙 그대로다.
 * 글머리 점은 `.scax-agenda-block__lines`(`list-style:disc`)가 그린다. `<li>` 자체에 flex 를 걸면
 * 점이 사라지므로 `.scax-note-line` 은 `<li>` 안쪽 칸에 건다.
 */
/**
 * 읽는 줄 하나. `chips` 는 그 줄이 딛는 근거 구간들의 시작 시각(회의 경과, D50)이고, 누르면 스크립트의 그 자리가
 * 열린다. 구간이 여럿이면 «전부» 선다 (D49) — 하나만 내면 나머지 근거는 닿을 길이 없다.
 * 근거가 없는 줄은 그 자리를 비운다 — 없는 시각을 지어내지 않는다.
 */
export type AgendaLineChip = { label: string; onJump?: () => void };
export type AgendaLineView = { text: string; chips?: AgendaLineChip[] };

export type AgendaLineEdit = {
  lines: string[];
  onChange: (index: number, value: string) => void;
  onRemove: (index: number) => void;
  onAdd: () => void;
};

/** 후보 한 줄 — 할 일 · 기한(있을 때만) · 조작 (결정 D19-3: 담당 후보 칸은 없다). */
export type AgendaTodoView = {
  key: string;
  what: string;
  /** 기한 후보. 없으면 그 자리를 비운다 — 「미정」 같은 말을 지어내지 않는다. */
  due?: ReactNode;
  actions?: ReactNode;
};

export function AgendaBlock({
  index,
  title,
  source,
  mark,
  lines,
  edit,
  todos,
  onRemove,
}: {
  /** 정렬된 목록에서의 자리. 번호(1부터)를 정한다 — 안건의 `order` 값을 쓰지 않는다. */
  index: number;
  title: string;
  source?: string | null;
  /** 「결론 남」·「결론 안 남」. 합성이 안 된 상태에서는 낼 값 자체가 없어 null 이다. */
  mark?: { text: string; concluded: boolean } | null;
  lines: AgendaLineView[];
  edit?: AgendaLineEdit | null;
  /** null 이면 「다음 할 일」 구획 자체가 없다. 빈 배열이면 구획은 서고 T12 가 온다. */
  todos?: AgendaTodoView[] | null;
  onRemove?: (() => void) | null;
}) {
  return (
    <section className="scax-agenda-block">
      <div className="scax-agenda-block__head">
        <h3 className="scax-agenda-block__title">{meetingScreen.agendaHead(index + 1, title)}</h3>
        {/* 「결론 남 / 안 남」은 낱말 자체가 뜻을 지고 있다 — 색으로 한 번 더 말하지 않는다 */}
        {mark && <span className="scax-agenda-block__source">{mark.text}</span>}
        {onRemove && <IconButton name="close" size={14} label={meetingScreen.dropAgenda} onClick={onRemove} />}
      </div>

      {source && <p className="scax-agenda-block__source">{source}</p>}

      {edit ? (
        /* 고치는 자리는 시안에 없다 — 줄 칸과 [내용 줄 추가]는 예전 모양 그대로 둔다 */
        <div>
          <ul style={{ display: "flex", flexDirection: "column", gap: 4, margin: 0, padding: 0, listStyle: "none" }}>
            {edit.lines.map((text, lineIndex) => (
              <li key={lineIndex} style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <input
                  aria-label={meetingScreen.linePlaceholder}
                  className="meeting-line-edit"
                  onChange={(event) => edit.onChange(lineIndex, event.target.value)}
                  placeholder={meetingScreen.linePlaceholder}
                  type="text"
                  value={text}
                />
                <IconButton name="close" size={14} label={meetingScreen.dropLine} onClick={() => edit.onRemove(lineIndex)} />
              </li>
            ))}
          </ul>
          <Button variant="text" size="sm" onClick={edit.onAdd} style={{ marginTop: 6 }} type="button">
            <Icon name="plus" size={14} /> {meetingScreen.addLine}
          </Button>
        </div>
      ) : (
        lines.length > 0 && (
          <ul className="scax-agenda-block__lines">
            {lines.map((line, lineIndex) => (
              <li key={lineIndex}>
                <span className="scax-note-line">
                  <span className="scax-note-line__text">{line.text}</span>
                  {/* 그 줄이 딛는 구간들 — 칩 하나가 구간 하나고, 누르면 스크립트의 그 자리가 열린다 (I05·D49) */}
                  {line.chips && line.chips.length > 0 && (
                    <span className="scax-note-line__evidence">
                      {line.chips.map((chip, chipIndex) => (
                        <TimeChip key={chipIndex} label={chip.label} onClick={chip.onJump} />
                      ))}
                    </span>
                  )}
                </span>
              </li>
            ))}
          </ul>
        )
      )}

      {/* E40 후속업무 후보 — 그 할 일이 나온 안건 안에 선다 */}
      {todos && (
        <div className="scax-agenda-block__todos">
          <p className="scax-agenda-block__todos-label">{meetingScreen.todos}</p>
          {todos.length === 0 ? (
            <p className="scax-agenda-block__source">{meetingScreen.noTodos}</p>
          ) : (
            <ul className="scax-agenda-block__todo-list">
              {todos.map((todo) => (
                <li className="scax-agenda-block__todo" key={todo.key}>
                  <span className="scax-agenda-block__todo-what">{todo.what}</span>
                  {todo.due && <span className="scax-agenda-block__todo-due tabular">{todo.due}</span>}
                  {/* 조작 자리는 폭을 고정한다 — 승격 여부로 버튼이 갈려도 행마다 세로로 맞아 선다 */}
                  {todo.actions && (
                    <span
                      className="scax-agenda-block__todo-state"
                      style={{ display: "flex", alignItems: "center", justifyContent: "flex-end", gap: 4, width: 116 }}
                    >
                      {todo.actions}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}

/** 값이 아직 없는 자리 — 회의록이 비어 있는 「실패」의 본문이 쓴다. */
export function AgendaEmptyBody() {
  return (
    <div>
      <EmptyValue />
    </div>
  );
}
