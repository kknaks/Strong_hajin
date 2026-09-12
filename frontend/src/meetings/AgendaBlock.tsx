import type { ReactNode } from "react";

import { Icon } from "../Icon";
import { TimeChip } from "../TimeChip";
import { EmptyValue } from "../Empty";
import { meetingScreen } from "../labels";

/**
 * 안건 한 덩어리 (CMP-105) — 회의 목록의 읽기 패널과 회의 상세가 **같은 블록**을 쓴다.
 *
 * 본문은 문단이 아니라 **줄 목록**이다 (D12): 줄 하나에 짧은 문장 하나, 종류(논의·결정) 배지는 두지 않는다.
 * 고칠 때도 같은 모양이다 — 줄 하나가 한 줄 칸이고, 아래에 [내용 줄 추가]가 선다.
 * 카드로 감싸지 않고 가는 선으로만 가른다 — 패널 안에 상자를 또 두면 글자가 두 번 들여쓰인다.
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
  compact,
}: {
  /** 정렬된 목록에서의 자리. 번호(1부터)와 위 테두리를 함께 정한다 — 안건의 `order` 값을 쓰지 않는다. */
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
  /** 목록 패널은 상세보다 한 단 좁게 선다. */
  compact?: boolean;
}) {
  const gap = compact ? 16 : 18;
  return (
    <section
      className="meeting-agenda"
      style={index === 0 ? { paddingBottom: gap } : { padding: `${gap}px 0`, borderTop: "1px solid var(--border-subtle)" }}
    >
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: 12 }}>
        <h3 style={{ flex: 1, minWidth: 0, fontSize: 14, fontWeight: 700 }}>{meetingScreen.agendaHead(index + 1, title)}</h3>
        {mark && (
          <span style={{ flex: "none", fontSize: 12, color: mark.concluded ? "var(--text-secondary)" : "var(--text-tertiary)" }}>
            {mark.text}
          </span>
        )}
        {onRemove && (
          <button
            aria-label={meetingScreen.dropAgenda}
            className="btn ghost icon h30"
            onClick={onRemove}
            style={{ flex: "none", width: 24, height: 24, color: "var(--text-tertiary)" }}
            type="button"
          >
            <Icon name="close" size={14} />
          </button>
        )}
      </div>

      {source && (
        <div className="t-meta" style={{ marginTop: 2, fontSize: 12 }}>
          {source}
        </div>
      )}

      {edit ? (
        <div style={{ marginTop: 6 }}>
          <ul style={{ display: "flex", flexDirection: "column", gap: 4, margin: 0, padding: 0, listStyle: "none" }}>
            {edit.lines.map((text, lineIndex) => (
              <li key={lineIndex} style={{ display: "flex", alignItems: "center", gap: 6, paddingLeft: 12 }}>
                <input
                  aria-label={meetingScreen.linePlaceholder}
                  className="meeting-line-edit"
                  onChange={(event) => edit.onChange(lineIndex, event.target.value)}
                  placeholder={meetingScreen.linePlaceholder}
                  type="text"
                  value={text}
                />
                <button
                  aria-label={meetingScreen.dropLine}
                  className="btn ghost icon h30"
                  onClick={() => edit.onRemove(lineIndex)}
                  style={{ width: 30, height: 30, color: "var(--text-tertiary)" }}
                  type="button"
                >
                  <Icon name="close" size={14} />
                </button>
              </li>
            ))}
          </ul>
          <button className="btn ghost h30" onClick={edit.onAdd} style={{ marginTop: 6, marginLeft: 12 }} type="button">
            <Icon name="plus" size={14} /> {meetingScreen.addLine}
          </button>
        </div>
      ) : (
        lines.length > 0 && (
          <ul style={{ display: "flex", flexDirection: "column", gap: 4, margin: "6px 0 0", padding: 0, listStyle: "none" }}>
            {lines.map((line, lineIndex) => (
              <li className="meeting-note-line" key={lineIndex}>
                <span style={{ flex: 1, minWidth: 0, fontSize: 13, color: "var(--text-secondary)", textWrap: "pretty" }}>
                  {line.text}
                </span>
                {/* 그 줄이 딛는 구간들 — 칩 하나가 구간 하나고, 누르면 스크립트의 그 자리가 열린다 (I05·D49) */}
                {line.chips && line.chips.length > 0 && (
                  <span className="meeting-note-chips">
                    {line.chips.map((chip, chipIndex) => (
                      <TimeChip key={chipIndex} label={chip.label} onClick={chip.onJump} />
                    ))}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )
      )}

      {/* E40 후속업무 후보 — 그 할 일이 나온 안건 안에 선다 */}
      {todos && (
        <div style={{ marginTop: 12 }}>
          <div style={{ paddingLeft: 12, fontSize: 13, fontWeight: 700 }}>{meetingScreen.todos}</div>
          {todos.length === 0 ? (
            <div className="t-meta" style={{ marginTop: 4, paddingLeft: 24, fontSize: 12 }}>
              {meetingScreen.noTodos}
            </div>
          ) : (
            <ul style={{ display: "flex", flexDirection: "column", gap: 6, margin: "6px 0 0", padding: 0, listStyle: "none" }}>
              {todos.map((todo) => (
                <li key={todo.key} style={{ display: "flex", alignItems: "center", gap: 12, minHeight: 30, paddingLeft: 24 }}>
                  <span style={{ flex: 1, minWidth: 0, fontSize: 13 }}>{todo.what}</span>
                  {todo.due && (
                    <span className="t-meta tabular" style={{ flex: "none", fontSize: 12 }}>
                      {todo.due}
                    </span>
                  )}
                  {/* 조작 자리는 폭을 고정한다 — 승격 여부로 버튼이 갈려도 행마다 세로로 맞아 선다 */}
                  {todo.actions && (
                    <span style={{ flex: "none", display: "flex", alignItems: "center", justifyContent: "flex-end", gap: 4, width: 116 }}>
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
    <div style={{ marginTop: 6, paddingLeft: 12 }}>
      <EmptyValue />
    </div>
  );
}
