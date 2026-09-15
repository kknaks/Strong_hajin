import { useRef, type ReactNode } from "react";

import { Button, IconButton } from "../../ds/Button";
import { InlineText, type InlineTextHandle } from "../../ds/InlineText";
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
export type AgendaLineView = {
  text: string;
  chips?: AgendaLineChip[];
  /**
   * 이 줄을 **제자리에서** 고친다 (백엔드 `6a9c41a`). 주면 글자가 눌러서 고치는 자리가 되고,
   * 안 주면 마크업이 예전 그대로다 — 못 고치는 줄에 고칠 수 있다는 구조를 남기지 않는다.
   * 여는 것은 부르는 쪽이 서버의 `can_edit_agendas.memo` 로 정한다.
   */
  edit?: ((next: string) => void | Promise<void>) | null;
  /** 이 줄을 걷는다. 확인을 묻지 않는다 — 자기가 적은 임시 재료다. */
  onRemove?: (() => void) | null;
};

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
  titleEdit,
  titlePlaceholder,
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
  /**
   * 제목을 **제자리에서** 고치는 자리 (2026-09-15 사용자 결정). 주면 제목 글자가 눌러서 고치는
   * 칸이 되고, 안 주면 머리 markup 이 **한 글자도 달라지지 않는다** — 고칠 수 없는 자리에
   * 「고칠 수 있다」는 구조를 남기지 않기 위해서다.
   *
   * 열지 말지는 부르는 쪽이 **서버의 `can_edit_agendas`** 로 정한다. 부품은 권한을 모른다.
   */
  titleEdit?: ((next: string) => void | Promise<void>) | null;
  /**
   * 제목이 아직 자리표시인가 (§12 R-50). 참이면 서버가 빈 제목을 낸 것이고, 머리는 번호만 낸다 —
   * 「안건 1. 안건 1」로 번호가 두 번 붙던 자리다. 값은 부르는 쪽이 서버에서 받아 넘긴다.
   */
  titlePlaceholder?: boolean;
  source?: string | null;
  /** 「결론 남」·「결론 안 남」. 합성이 안 된 상태에서는 낼 값 자체가 없어 null 이다. */
  mark?: { text: string; concluded: boolean } | null;
  lines: AgendaLineView[];
  edit?: AgendaLineEdit | null;
  /** null 이면 「다음 할 일」 구획 자체가 없다. 빈 배열이면 구획은 서고 T12 가 온다. */
  todos?: AgendaTodoView[] | null;
  onRemove?: (() => void) | null;
}) {
  /* 제목 행이 클릭을 받아 제자리 편집을 연다 — 손잡이를 담아 둘 상자 (2026-09-15 사용자 결정) */
  const titleHandle = useRef<InlineTextHandle | null>(null);
  return (
    <section className="scax-agenda-block">
      <div className="scax-agenda-block__head">
        <h3
          className={[
            "scax-agenda-block__title",
            titlePlaceholder ? "scax-agenda-block__title--placeholder" : "",
            /* 누를 수 있는 «행» 이라는 신호는 커서 하나다 — 바탕·밑줄을 지어내지 않는다 */
            titleEdit ? "scax-agenda-block__title--editable" : "",
          ]
            .filter(Boolean)
            .join(" ")}
          /*
           * **행 전체가 과녁이다.** 제목 오른쪽 빈 자리를 눌러도 편집이 열리고, 캐럿은 누른
           * 자리에서 가장 가까운 글자 사이에 놓인다(빈 자리면 글자 끝).
           *
           * 글자(`span`)를 늘리지 않았다 — 이 머리는 「안건 1. 제목」이 한 줄로 흐르는 자리라
           * 늘리면 둘 사이 간격이 바뀌어 **글자가 움직인다.** 그래서 부모가 클릭만 받는다:
           * **높이·여백·배경을 하나도 더하지 않았으므로 레이아웃은 한 픽셀도 안 바뀐다.**
           *
           * 단추 위는 제외한다 — `×` 나 그 밖의 조작을 눌렀는데 편집이 열리면 안 된다.
           */
          onClick={
            titleEdit
              ? (event) => {
                  if ((event.target as HTMLElement).closest("button")) return;
                  titleHandle.current?.open({ x: event.clientX, y: event.clientY });
                }
              : undefined
          }
        >
          {titleEdit ? (
            /* 번호는 그대로 글자로 남고 **제목만** 칸이 된다 — 「안건 1.」을 사람이 고치는 일은 없다.
               활자는 이 `h3` 의 것을 그대로 물려받으므로 칸이 열려도 줄이 흔들리지 않는다. */
            <span className="scax-agenda-block__title-row">
              {`${meetingScreen.agendaHeadNo(index + 1, Boolean(title))} `}
              <InlineText
                handle={titleHandle}
                label={meetingScreen.agendaTitleEdit}
                onCommit={titleEdit}
                placeholder={meetingScreen.agendaTitleEmpty}
                value={title}
              />
            </span>
          ) : (
            meetingScreen.agendaHead(index + 1, title)
          )}
        </h3>
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
                  <span className="scax-note-line__text">
                    {line.edit ? (
                      /* 줄의 «빈 자리» 를 눌러도 열린다 — 짧은 줄일수록 글자만한 과녁은 빗나간다.
                         이 칸(`.scax-note-line__text`)은 이미 늘어나 있으므로 그 너비를 채우기만 한다 */
                      <InlineText fill label={meetingScreen.memoLineEdit} onCommit={line.edit} value={line.text} />
                    ) : (
                      line.text
                    )}
                  </span>
                  {line.onRemove && (
                    <IconButton name="close" size={12} label={meetingScreen.memoLineDrop} onClick={line.onRemove} />
                  )}
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
