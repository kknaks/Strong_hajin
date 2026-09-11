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
      className={`assistant-character ${size} ${state.kind}`}
      data-character-key={asset.key}
      data-assistant-state={state.kind}
      role="img"
    >
      {!imageReady && (
        <span aria-hidden className="assistant-character-fallback">{asset.fallbackLabel}</span>
      )}
      <img
        alt=""
        className={imageReady ? "is-ready" : ""}
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
}: {
  state: AssistantPresentationState;
  onOpen: () => void;
  onPrefill: (prompt: string) => void;
  characterKey?: string | null;
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

  return (
    <div
      className={`assistant-launcher ${visibleState.kind}${documentVisible ? "" : " motion-paused"}`}
      data-assistant-state={visibleState.kind}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <button
        aria-label="AX"
        className="assistant-launcher-character"
        onClick={onOpen}
        type="button"
      >
        <AssistantCharacter characterKey={characterKey} state={visibleState} />
      </button>
      <span aria-live="polite" className="sr-only" role="status">AX 상태: {visibleState.label}</span>
      <button
        aria-label={visibleState.prompt}
        className="assistant-launcher-bubble"
        onClick={() => {
          onPrefill(visibleState.prompt);
          onOpen();
        }}
        type="button"
      >
        <small><span aria-hidden className="assistant-state-dot" />{visibleState.label}</small>
        <span>{visibleState.prompt}</span>
      </button>
    </div>
  );
}
