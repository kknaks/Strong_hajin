import { useEffect, useRef, useState } from "react";

import { personName } from "./labels";
import type { GraphEdge, GraphNode } from "./viewModels";

/**
 * 하나의 renderer, 두 가지 쓰임.
 *
 * 전체 관계 탐색에서는 사람이 끌고 돌리며 탐색하는 화면이고, 채팅 카드에서는 그 turn이 실제로 조회한 것을 고정한
 * snapshot이다. 둘 다 서버가 인가해 내려준 같은 node/edge를 그리며, 여기서 그리는 어떤 것도 원장을 바꾸지 않는다 —
 * hover 강조도 node를 끄는 것도 화면 상태일 뿐이다.
 *
 * 그림 옆에는 언제나 같은 내용의 목록이 함께 있다. 스크린 리더와 WebGL이 없는 환경에서는 그 목록이 이 화면이다.
 */

const KIND_COLOR: Record<string, string> = {
  person: "#4c6ef5",
  team: "#7048e8",
  task: "#0ca678",
  work_request: "#f59f00",
  material: "#868e96",
  meeting: "#e8590c",
};

export const KIND_LABEL: Record<string, string> = {
  person: "사람",
  team: "팀",
  work_request: "업무 요청",
  task: "업무",
  material: "자료",
  meeting: "회의",
};

const refOf = (node: GraphNode): string => `${node.kind}:${node.id}`;

/** A stable starting position, so the same answer is laid out the same way every time it is shown. */
function seed(index: number, total: number): { x: number; y: number } {
  const angle = (2 * Math.PI * index) / Math.max(1, total);
  return { x: Math.cos(angle) * (1 + (index % 3) * 0.25), y: Math.sin(angle) * (1 + (index % 3) * 0.25) };
}

export function GraphCanvas({
  nodes,
  edges,
  centerRef,
  onSelect,
  interactive = true,
  height = 460,
}: {
  nodes: GraphNode[];
  edges: GraphEdge[];
  centerRef?: string;
  onSelect?: (node: GraphNode) => void;
  /** A chat card is a fixed picture of one turn: no panning, no zooming, no dragging. */
  interactive?: boolean;
  height?: number;
}) {
  const container = useRef<HTMLDivElement | null>(null);
  const [drawn, setDrawn] = useState(false);
  const [hovered, setHovered] = useState<string | null>(null);
  const select = useRef(onSelect);
  select.current = onSelect;

  useEffect(() => {
    const element = container.current;
    if (!element) return;
    let cancelled = false;
    let renderer: { kill: () => void } | null = null;

    const draw = (
      Graph: typeof import("graphology").default,
      forceAtlas2: typeof import("graphology-layout-forceatlas2").default,
      Sigma: typeof import("sigma").default,
    ) => {
    const graph = new Graph({ multi: true, type: "directed" });
    nodes.forEach((node, index) => {
      const ref = refOf(node);
      if (graph.hasNode(ref)) return;
      graph.addNode(ref, {
        ...seed(index, nodes.length),
        label: node.kind === "person" ? personName(node.title) : node.title,
        size: ref === centerRef ? 14 : 8,
        color: KIND_COLOR[node.kind] ?? "#868e96",
        kind: node.kind,
      });
    });
    for (const edge of edges) {
      if (!graph.hasNode(edge.from) || !graph.hasNode(edge.to)) continue;
      graph.addEdge(edge.from, edge.to, {
        label: edge.count && edge.count > 1 ? `${edge.label ?? edge.kind} ×${edge.count}` : edge.label ?? edge.kind,
        size: Math.min(4, 1 + (edge.count ?? 1) * 0.5),
        type: "arrow",
        color: "#ced4da",
      });
    }
    if (graph.order > 1) {
      forceAtlas2.assign(graph, { iterations: 200, settings: { ...forceAtlas2.inferSettings(graph), gravity: 1.2 } });
    }

    let sigma;
    try {
      sigma = new Sigma(graph, element, {
        renderEdgeLabels: interactive,
        enableEdgeEvents: false,
        allowInvalidContainer: true,
        defaultEdgeType: "arrow",
      });
    } catch {
      setDrawn(false);
      return null;
    }
    setDrawn(true);
    if (!interactive) {
      // A chat card is a picture of one turn, not a place to explore: nothing pans, zooms or drags.
      sigma.getMouseCaptor().kill();
      sigma.getTouchCaptor().kill();
    }
    sigma.on("clickNode", ({ node }: { node: string }) => {
      const found = nodes.find((candidate) => refOf(candidate) === node);
      if (found) select.current?.(found);
    });
    sigma.on("enterNode", ({ node }: { node: string }) => setHovered(node));
    sigma.on("leaveNode", () => setHovered(null));
    return sigma;
    };

    // The renderer needs WebGL, so it is loaded only when there is a screen to draw on. Where there is not — a test,
    // a browser refusing a context — the list beside this is the answer, not an error.
    void (async () => {
      const [{ default: Graph }, { default: forceAtlas2 }, { default: Sigma }] = await Promise.all([
        import("graphology"),
        import("graphology-layout-forceatlas2"),
        import("sigma"),
      ]);
      if (cancelled || !container.current) return;
      renderer = draw(Graph, forceAtlas2, Sigma);
    })().catch(() => setDrawn(false));

    return () => {
      cancelled = true;
      renderer?.kill();
    };
  }, [nodes, edges, centerRef, interactive]);

  // Hovering a node dims everything it is not connected to. It is a way of looking, not a change to the answer.
  useEffect(() => {
    if (!container.current) return;
    container.current.dataset.hovered = hovered ?? "";
  }, [hovered]);

  return (
    <div className="graph-canvas-shell">
      <div
        aria-hidden={drawn ? undefined : true}
        className="graph-canvas"
        data-drawn={drawn ? "true" : "false"}
        data-node-count={nodes.length}
        ref={container}
        style={{ height }}
      />
      {!drawn && (
        <p className="t-meta">
          이 화면에서는 그림을 그릴 수 없어 아래 목록으로 보여 줍니다.
        </p>
      )}
    </div>
  );
}
