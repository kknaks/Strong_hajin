import { decodeString } from "micromark-util-decode-string";
import type { AnswerDocument } from "../viewModels";

type Node = {
  type: string;
  value?: string;
  children?: Node[];
  position?: { start: { offset?: number }; end: { offset?: number } };
  data?: { hName: string; hProperties: Record<string, string> };
};

/** Only parser-produced text is an insertion site; raw HTML, links and code stay protected. */
export function answerElementsPlugin(document: AnswerDocument, body: string) {
  const elements = new Map(document.elements.map((element) => [element.key, element]));
  const elementNode = (key: string, block: boolean): Node => ({
    type: "answerElement",
    data: { hName: block ? "div" : "span", hProperties: { "data-answer-element": key } },
    children: [],
  });

  function markers(node: Node) {
    const start = node.position?.start.offset;
    const end = node.position?.end.offset;
    if (node.type !== "text" || start === undefined || end === undefined) return [];
    const raw = body.slice(start, end);
    return [...raw.matchAll(/\{\{([^{}\r\n]*)\}\}/g)].flatMap((match) => {
      const prefix = raw.slice(0, match.index);
      const slashes = prefix.match(/\\+$/)?.[0].length ?? 0;
      if (slashes % 2) return [];
      // AST values have decoded escapes/entities, so source offsets cannot be used directly.
      const index = decodeString(prefix).length;
      if (node.value?.slice(index, index + match[0].length) !== match[0]) return [];
      return [{ key: match[1], index, length: match[0].length }];
    });
  }

  return () => (tree: Node) => {
    function visit(node: Node) {
      if (!node.children || ["link", "linkReference", "code", "inlineCode", "html"].includes(node.type)) return;
      node.children = node.children.flatMap((child) => {
        if (child.type === "paragraph" && child.children?.length === 1) {
          const text = child.children[0];
          const [marker] = markers(text);
          if (marker && text.value?.trim() === `{{${marker.key}}}` && elements.get(marker.key)?.type === "resource_list") {
            return [elementNode(marker.key, true)];
          }
        }
        const found = markers(child);
        if (!found.length) {
          visit(child);
          return [child];
        }
        const result: Node[] = [];
        let cursor = 0;
        for (const marker of found) {
          result.push({ type: "text", value: child.value!.slice(cursor, marker.index) });
          result.push(elementNode(marker.key, false));
          cursor = marker.index + marker.length;
        }
        result.push({ type: "text", value: child.value!.slice(cursor) });
        return result;
      });
    }
    visit(tree);
  };
}
