import bearSrc from "./assets/assistant-character/bear.avif";
import calicoCatSrc from "./assets/assistant-character/calico-cat.avif";
import chickSrc from "./assets/assistant-character/chick.avif";
import creamCatSrc from "./assets/assistant-character/cream-cat.avif";
import puppySrc from "./assets/assistant-character/puppy.avif";
import rabbitSrc from "./assets/assistant-character/rabbit.avif";
import redPandaSrc from "./assets/assistant-character/red-panda.avif";
import silverTabbySrc from "./assets/assistant-character/silver-tabby.avif";
import tuxedoCatSrc from "./assets/assistant-character/tuxedo-cat.avif";


export type AssistantCharacterAsset = {
  key: string;
  name: string;
  fallbackLabel: string;
  src: string;
  productionApproved: boolean;
};

export const assistantCharacterCatalog: readonly AssistantCharacterAsset[] = [
  { key: "cream-cat", name: "크림 고양이", fallbackLabel: "크", src: creamCatSrc, productionApproved: true },
  { key: "silver-tabby", name: "실버 태비", fallbackLabel: "실", src: silverTabbySrc, productionApproved: true },
  { key: "tuxedo-cat", name: "턱시도", fallbackLabel: "턱", src: tuxedoCatSrc, productionApproved: true },
  { key: "calico-cat", name: "삼색 고양이", fallbackLabel: "삼", src: calicoCatSrc, productionApproved: true },
  { key: "puppy", name: "강아지", fallbackLabel: "강", src: puppySrc, productionApproved: true },
  { key: "rabbit", name: "토끼", fallbackLabel: "토", src: rabbitSrc, productionApproved: true },
  { key: "bear", name: "곰", fallbackLabel: "곰", src: bearSrc, productionApproved: true },
  { key: "chick", name: "병아리", fallbackLabel: "병", src: chickSrc, productionApproved: true },
  { key: "red-panda", name: "레서판다", fallbackLabel: "레", src: redPandaSrc, productionApproved: true },
] as const;

export const defaultAssistantCharacterAsset = assistantCharacterCatalog[0];

export function resolveAssistantCharacter(characterKey: string | null | undefined): AssistantCharacterAsset {
  return assistantCharacterCatalog.find((asset) => asset.key === characterKey) ?? defaultAssistantCharacterAsset;
}
