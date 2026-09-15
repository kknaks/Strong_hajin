import { useEffect, useRef, useState } from "react";

/**
 * 제자리 편집 한 칸 (CMP-118) — **입력칸이 나타나지 않는다.**
 *
 * 계약이 세 줄이다 (사용자 결정 2026-09-15):
 *   · 입력칸이 나타나지 않는다. 테두리·바탕·그림자·둥근 모서리 어느 것도 생기지 않는다
 *   · 글자가 1px 도 움직이지 않는다
 *   · 바뀌는 것은 **캐럿이 깜박이는 것 하나**뿐이다
 *
 * ── 왜 `<input>` 이 아닌가 ──
 * 처음에는 닫힌 `<button>` 과 열린 `<input>` 을 갈아 끼웠다. 활자·여백을 아무리 맞춰도 그것은
 * **다른 노드**라 계약을 지킬 수가 없다: `input` 은 자기 나름의 기본 크기(`size` 속성)·기준선·
 * 안여백을 갖고, 「1px 도 안 움직인다」를 CSS 로 쫓는 싸움이 된다. 실물에서 실제로 밀렸다.
 *
 * 그래서 **노드를 바꾸지 않는다.** 글자를 이고 있던 바로 그 `<span>` 이 `contenteditable` 로
 * 바뀐다 — 같은 노드·같은 활자·같은 여백이고, 달라지는 것은 **캐럿이 생긴다는 것 하나**다.
 * 「안 움직인다」를 CSS 로 흉내 내는 대신, 움직일 대상 자체를 없앴다.
 *
 * ── 여러 줄을 받지 않는다 ──
 * `Enter` 는 줄바꿈이 아니라 **저장**이고 (`preventDefault`), 붙여넣기로 들어온 줄바꿈은
 * 저장할 때 걷는다 — `contenteditable` 은 `<input>` 과 달리 줄바꿈이 «구조적으로» 막히지 않으므로
 * 그 자리를 우리가 지킨다.
 *
 * ── 포커스 표시가 없다 ──
 * 전역 결정으로 포커스 링이 앱 전체에서 사라졌다 (2026-09-15). 이 부품도 예외가 아니다 —
 * `:focus-visible` 규칙을 두지 않는다. 열렸다는 것을 말하는 것은 **캐럿뿐**이다.
 *
 * ── 저장은 부르는 쪽의 일 ──
 * 값을 들고 있지 않다. `value` 는 늘 서버가 말한 것이고, `onCommit` 이 거절되면 그 값이 그대로
 * 남아 **글자가 저절로 원래대로 돌아온다.** 되돌리는 코드가 따로 없다.
 */
/**
 * 부르는 쪽이 «행» 에서 이 자리를 여는 손잡이 (2026-09-15 사용자 결정).
 *
 * 과녁을 넓히는 길이 둘인데 **늘리는 쪽은 안 되는 자리가 있다** — 이 글자가 다른 글자 사이에
 * 끼어 흐르면(안건 머리의 「안건 1. 제목」) 블록으로 바꾸는 순간 줄이 갈라지고, flex 로 가르면
 * 글자 사이 공백이 `gap` 으로 바뀌어 **글자가 움직인다.** 그래서 늘리지 않고, **부모 행이
 * 클릭을 받아 이 손잡이를 부른다.** 레이아웃에 더해지는 것이 하나도 없다.
 */
export type InlineTextHandle = {
  /** 편집을 연다. `point` 를 주면 **캐럿이 누른 자리에서 가장 가까운 글자 사이**에 놓인다. */
  open: (point?: { x: number; y: number }) => void;
};

export function InlineText({
  value,
  placeholder,
  label,
  fill,
  handle,
  onCommit,
}: {
  /** 지금 서버가 말하는 값. 저장이 거절되면 화면은 이 값으로 돌아온다. */
  value: string;
  /** 값이 비어 있을 때 흐리게 서는 말. */
  placeholder?: string;
  /** 읽어 주는 이름. */
  label: string;
  /**
   * **누를 수 있는 범위를 제 칸 전체로 넓힌다** — 글자 오른쪽 빈 자리를 눌러도 편집이 열린다.
   *
   * 기본값(`display:inline`)은 글자 폭만큼만 눌린다. 짧은 줄일수록 과녁이 작아서, 「눌러서
   * 고친다」를 아는 사람도 빗나간다. 참이면 `display:block;width:100%` 가 되어 **부모 칸이
   * 내주는 너비를 그대로 채운다.**
   *
   * ⚠ **레이아웃을 바꾸는 값이 아니다.** 글자는 여전히 칸의 왼쪽 위 같은 자리에서 시작하고
   * 활자·줄높이·여백이 그대로다 — 넓어지는 것은 «누를 수 있는 넓이» 뿐이다. 다만 블록이 되므로
   * **부모가 블록 칸일 때만** 준다: 글자 중간에 끼어 흐르는 자리(안건 머리의 「안건 1. 제목」)에
   * 주면 줄이 갈라진다.
   */
  fill?: boolean;
  /**
   * 부모 행이 이 자리를 열 수 있게 손잡이를 담아 둘 상자. 「행을 눌러도 열린다」가 필요한
   * 자리만 준다 — 안 주면 예전처럼 **이 글자를 직접 눌렀을 때만** 열린다.
   */
  handle?: { current: InlineTextHandle | null };
  /** 고친 값을 맡는다. 거절되면 글자가 원래대로 돌아온다. */
  onCommit: (next: string) => void | Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  /* 보낸 값이 서버를 돌아 `value` 로 다시 올 때까지 «보낸 값» 을 보여 준다 — 그 사이에 옛 글자가
     한 번 깜빡이지 않게. 실패하면 이것을 비우고, 그 순간 화면은 `value`(원래 글자)로 돌아온다. */
  const [sent, setSent] = useState<string | null>(null);
  const node = useRef<HTMLSpanElement | null>(null);
  /** `Esc` 로 닫았는가 — 초점이 빠지며 도는 `blur` 저장을 건너뛴다. */
  const cancelled = useRef(false);
  /** 열면서 캐럿을 놓을 자리 (화면 좌표). 없으면 글자 끝이다. */
  const caretPoint = useRef<{ x: number; y: number } | null>(null);
  const shown = sent ?? value;

  function open(point?: { x: number; y: number }) {
    cancelled.current = false;
    caretPoint.current = point ?? null;
    setEditing(true);
  }

  /* 부모 행이 부를 수 있게 손잡이를 담아 둔다. 렌더 중에 담아도 되는 값이라(부수효과가 아니라
     같은 인스턴스를 가리키는 참조다) effect 로 미루지 않는다 — 미루면 «첫 클릭» 이 비어 있다. */
  if (handle) handle.current = { open };

  /* 글자는 **열 때 한 번만** 우리가 넣는다. React 가 이 노드의 자식을 매 렌더마다 맞추면
     사람이 치는 동안 캐럿이 맨 앞으로 튄다 — 그래서 여는 동안은 children 을 비우고(아래)
     내용을 직접 다룬다. 캐럿은 **글자 끝**에 둔다: 전체 선택을 하면 파란 칠이 생겨 계약을 깬다. */
  useEffect(() => {
    const element = node.current;
    if (!editing || !element) return;
    element.textContent = shown;
    element.focus();
    const point = caretPoint.current;
    caretPoint.current = null;
    try {
      const selection = window.getSelection();
      /* 누른 자리에서 «가장 가까운 글자 사이» 로 — 브라우저가 그 계산을 해 준다.
         이름이 둘로 갈려 있어 둘 다 본다(WebKit 계열은 `caretRangeFromPoint`). */
      let range: Range | null = null;
      if (point) {
        const api = document as unknown as {
          caretRangeFromPoint?: (x: number, y: number) => Range | null;
          caretPositionFromPoint?: (x: number, y: number) => { offsetNode: Node; offset: number } | null;
        };
        if (api.caretRangeFromPoint) {
          range = api.caretRangeFromPoint(point.x, point.y);
        } else if (api.caretPositionFromPoint) {
          const spot = api.caretPositionFromPoint(point.x, point.y);
          if (spot) {
            range = document.createRange();
            range.setStart(spot.offsetNode, spot.offset);
          }
        }
        /* 글자 «밖» 을 눌렀으면(제목 오른쪽 빈 자리) 그 자리는 이 칸의 것이 아니다 — 글자 끝으로 간다 */
        if (range && !element.contains(range.startContainer)) range = null;
      }
      if (range) {
        range.collapse(true);
      } else {
        /* 누른 자리를 못 잡았거나 글자 밖이었다 — **글자 끝**이다.
           전체 선택을 하지 않는다: 파란 칠이 생기면 「캐럿만 깜박인다」는 계약이 깨진다. */
        range = document.createRange();
        range.selectNodeContents(element);
        range.collapse(false);
      }
      selection?.removeAllRanges();
      selection?.addRange(range);
    } catch {
      /* 선택 API 가 없는 환경(테스트 등) — 캐럿 자리는 부차적이라 조용히 넘긴다 */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editing]);

  function close() {
    setEditing(false);
  }

  function commit() {
    if (cancelled.current) return;
    /* 붙여넣기로 들어온 줄바꿈을 걷는다 — 이 자리는 한 줄이다 */
    const next = (node.current?.textContent ?? "").replace(/\s+/g, " ").trim();
    close();
    /* 빈 값은 저장하지 않는다 — 이름을 지우는 것은 이름을 바꾸는 것과 다른 일이고,
       이 자리에는 「지우기」가 없다. 값이 그대로여도 요청을 내지 않는다. */
    if (next.length === 0 || next === shown) return;
    setSent(next);
    void Promise.resolve(onCommit(next))
      .catch(() => undefined)
      .finally(() => setSent(null));
  }

  return (
    <span
      aria-label={label}
      className={[
        "scax-inline-text",
        fill ? "scax-inline-text--fill" : "",
        shown || editing ? "" : "scax-inline-text--empty",
      ]
        .filter(Boolean)
        .join(" ")}
      contentEditable={editing}
      onBlur={editing ? commit : undefined}
      onClick={editing ? undefined : (event) => open({ x: event.clientX, y: event.clientY })}
      onKeyDown={(event) => {
        if (!editing) {
          /* 닫힌 동안은 «누르는 자리» 다 — 키보드로도 같은 걸음이 되게 한다 */
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            /* 키보드로 열면 누른 자리가 없다 — 글자 끝이다 */
            open();
          }
          return;
        }
        if (event.key === "Enter") {
          /* 줄바꿈이 아니라 저장이다 */
          event.preventDefault();
          commit();
          return;
        }
        if (event.key === "Escape") {
          event.preventDefault();
          cancelled.current = true;
          close();
        }
      }}
      ref={node}
      role={editing ? "textbox" : "button"}
      suppressContentEditableWarning
      tabIndex={0}
    >
      {/* 여는 동안은 비운다 — 내용은 위 effect 가 직접 넣고, 사람이 치는 동안 React 가 건드리지 않는다 */}
      {editing ? null : shown || placeholder}
    </span>
  );
}
