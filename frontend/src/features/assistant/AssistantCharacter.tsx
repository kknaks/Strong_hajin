import { useEffect, useState } from "react";

import { resolveAssistantCharacter } from "./assistantCharacterAssets";
import type { AssistantPresentationState } from "./assistantPresentation";


export function AssistantCharacter({
  state,
  size = "launcher",
  characterKey,
}: {
  state: AssistantPresentationState;
  size?: "launcher" | "header" | "profile";
  characterKey?: string | null;
}) {
  const asset = resolveAssistantCharacter(characterKey);
  const [loadedSrc, setLoadedSrc] = useState<string | null>(null);
  const imageReady = loadedSrc === asset.src;
  return (
    <span
      aria-label={`AX assistant · ${asset.name} · ${state.label}`}
      className={`scax-character scax-character--${size} scax-character--${state.kind}`}
      data-character-key={asset.key}
      data-assistant-state={state.kind}
      role="img"
    >
      {!imageReady && (
        <span aria-hidden className="scax-character__fallback">{asset.fallbackLabel}</span>
      )}
      <img
        alt=""
        className={imageReady ? "scax-character__img scax-character__img--ready" : "scax-character__img"}
        draggable={false}
        onError={() => setLoadedSrc(null)}
        onLoad={() => setLoadedSrc(asset.src)}
        src={asset.src}
      />
    </span>
  );
}

export function AssistantLauncher({
  state,
  onOpen,
  onPrefill,
  characterKey,
  bubble = true,
}: {
  state: AssistantPresentationState;
  onOpen: () => void;
  onPrefill: (prompt: string) => void;
  characterKey?: string | null;
  /**
   * 말풍선을 세울까 (4차 발주 7).
   *
   * 업무 화면처럼 **화면 아래에서 토스트가 말하는** 자리에서는 접는다 — 어두운 알약에 짧은 글 한 줄이
   * 토스트와 거의 같은 모양이라, 나란히 뜨면 「방금 명령의 결과」로 읽힌다. 캐릭터와 그 디자인은
   * 그대로다: 오브를 누르면 지금까지처럼 AX 가 열리고, 상태는 `aria-live` 로 계속 읽힌다.
   */
  bubble?: boolean;
}) {
  const [hovered, setHovered] = useState(false);
  const [documentVisible, setDocumentVisible] = useState(() => document.visibilityState !== "hidden");
  useEffect(() => {
    const updateVisibility = () => setDocumentVisible(document.visibilityState !== "hidden");
    document.addEventListener("visibilitychange", updateVisibility);
    return () => document.removeEventListener("visibilitychange", updateVisibility);
  }, []);
  const visibleState: AssistantPresentationState = hovered && state.kind === "idle"
    ? { kind: "hover", label: "AX와 대화", prompt: state.prompt }
    : state;

  // 시안의 AgentBubble (handoff `scax-ui.jsx`) — 말풍선이 오브 «위» 에 서고 오브 뒤로 빛이 번진다.
  // 시안의 오브 이미지(assets/ai-agent-orb.png)는 핸드오프 다운로드가 실패해 우리에게 없다.
  // 우리 캐릭터 에셋을 그 자리에 세운다 — 사용자가 고른 캐릭터가 곧 이 화면의 오브다.
  return (
    <div
      className={`scax-agent scax-agent--ax scax-agent--${visibleState.kind}${bubble ? "" : " scax-agent--nobubble"}${documentVisible ? "" : " scax-agent--motion-paused"}`}
      data-assistant-state={visibleState.kind}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      {bubble && (
        <button
          aria-label={visibleState.prompt}
          className="scax-agent__bubble"
          onClick={() => {
            onPrefill(visibleState.prompt);
            onOpen();
          }}
          type="button"
        >
          <small><span aria-hidden className="scax-agent__dot" />{visibleState.label}</small>
          <span>{visibleState.prompt}</span>
        </button>
      )}
      <span aria-live="polite" className="sr-only" role="status">AX 상태: {visibleState.label}</span>
      <div className="scax-agent__orb">
        <span aria-hidden className="scax-agent__glow" />
        <button aria-label="AX" className="scax-agent__launch" onClick={onOpen} type="button">
          <AssistantCharacter characterKey={characterKey} state={visibleState} />
        </button>
      </div>
    </div>
  );
}
