import { useEffect, useRef, useState } from "react";

import { personName } from "../../lib/labels";
import type { GraphEdge, GraphNode } from "../../lib/viewModels";

/**
 * 하나의 renderer, 두 가지 쓰임.
 *
 * 전체 관계 탐색에서는 사람이 끌고 돌리며 탐색하는 화면이고, 채팅 카드에서는 그 turn이 실제로 조회한 것을 고정한
 * snapshot이다. 둘 다 서버가 인가해 내려준 같은 node/edge를 그린다.
 *
 * 여기서 하는 어떤 것도 원장을 바꾸지 않는다. hover로 이웃을 밝히는 것도, node를 끌어 옮기는 것도 화면 상태일
 * 뿐이다. 배치는 첫 렌더 전에 한 번 계산해 두므로 화면이 정돈되는 과정을 사용자가 지켜보지 않는다.
 *
 * 그림 옆에는 언제나 같은 내용의 목록이 함께 있다. 스크린 리더와 WebGL이 없는 환경에서는 그 목록이 이 화면이다.
 */

/**
 * sigma 는 WebGL 로 그려서 CSS 변수를 이해하지 못한다 — 문자열 색을 요구한다.
 * 그래서 값을 여기에 두지 않고 `:root` 의 토큰을 한 번 읽어서 넘긴다. 색의 주인은 `styles/scax.css` 다.
 */
/**
 * 그래프 색의 주인은 `styles/scax.css` 다 (`--scax-graph-*`). 여기서는 값을 갖지 않고 이름만 안다.
 * 바퀴 9 에서 구 `--graph-*` 를 그리로 올리며 **읽는 이 파일도 같이** 고쳤다 — 한쪽만 고치면
 * 테스트는 통과하는데 화면에서 색만 사라진다.
 *
 * 소비처가 둘이라 형태도 둘이다 — 범례 같은 DOM 은 `var(...)` 를 그대로 쓰고, sigma 는 WebGL 로
 * 그려서 CSS 변수를 이해하지 못하므로 계산된 문자열을 넘겨야 한다.
 */
const NODE_TOKEN: Record<string, string> = {
  person: "--scax-graph-node-person",
  team: "--scax-graph-node-team",
  project: "--scax-graph-node-project",
  work_request: "--scax-graph-node-work-request",
  task: "--scax-graph-node-task",
  material: "--scax-graph-node-material",
  meeting: "--scax-graph-node-meeting",
  report: "--scax-graph-node-report",
};

/** DOM 에서 쓰는 형태. 인라인 style 에 그대로 넣으면 브라우저가 푼다. */
export const KIND_COLOR: Record<string, string> = Object.fromEntries(
  Object.entries(NODE_TOKEN).map(([kind, name]) => [kind, `var(${name})`]),
);

const tokenCache = new Map<string, string>();
/** sigma 에 넘길 형태. 노드마다 다시 재지 않도록 이름당 한 번만 읽는다. */
const token = (name: string): string => {
  if (typeof document === "undefined") return "";
  const cached = tokenCache.get(name);
  if (cached !== undefined) return cached;
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  tokenCache.set(name, value);
  return value;
};
const nodeColor = (kind: string): string => token(NODE_TOKEN[kind] ?? "--scax-graph-node-fallback");

export const KIND_LABEL: Record<string, string> = {
  person: "사람",
  team: "팀",
  project: "프로젝트",
  work_request: "업무 요청",
  task: "업무",
  material: "자료·산출물",
  meeting: "회의",
  report: "보고",
};

/** 한 연결을 그림 위에 적을 때의 짧은 이름. 그리는 자리는 좁으므로 방향은 화살표가 말한다. */
export const EDGE_LABEL: Record<string, string> = {
  produced: "만든 업무",
  requested: "보낸 요청",
  asked_of: "요청받은 사람",
  holds: "담당",
  parent_of: "하위 업무",
  refers_to: "참고 업무",
  has_material: "자료",
};

/**
 * 같은 연결을 문장으로 읽을 때의 이름. 어느 쪽에서 보느냐로 말이 달라지므로 방향마다 따로 둔다.
 *
 * 모르는 종류는 지어내지 않고 자기 이름을 그대로 쓴다.
 */
export const EDGE_SENTENCE: Record<string, { incoming: string; outgoing: string }> = {
  produced: { incoming: "이 업무를 만든 요청", outgoing: "이 요청이 만든 업무" },
  requested: { incoming: "이 요청을 보낸 사람", outgoing: "보낸 요청" },
  asked_of: { incoming: "요청받은 사람", outgoing: "요청받은 사람" },
  holds: { incoming: "담당", outgoing: "담당 중인 업무" },
  parent_of: { incoming: "상위 업무", outgoing: "하위 업무" },
  refers_to: { incoming: "이 업무를 참고한 업무", outgoing: "참고 업무" },
  has_material: { incoming: "붙어 있는 업무", outgoing: "참고 자료·산출물" },
};

/** 정본이 어디에 있는지. 그래프는 이 원장들의 읽기 투영이고 자기 행을 갖지 않는다. */
export const KIND_SOURCE: Record<string, string> = {
  person: "Member + Membership + Appointment",
  team: "OrganizationUnit + Membership 집계",
  project: "Project + ProjectAssignment",
  work_request: "WorkRequest",
  task: "Task + TaskAssignment",
  material: "Attachment + AttachmentBinding",
  meeting: "Meeting + MeetingAttendee",
  report: "DailyReport",
};

const DIM_NODE = () => token("--scax-graph-node-dim");
const DIM_EDGE = () => token("--scax-graph-edge-dim");
const ACTIVE_EDGE = () => token("--scax-graph-edge-active");

export const refOf = (node: GraphNode): string => `${node.kind}:${node.id}`;

const shorten = (text: string, limit = 16): string => (text.length > limit ? `${text.slice(0, limit - 1)}…` : text);

export type GraphControls = {
  zoomIn: () => void;
  zoomOut: () => void;
  reset: () => void;
  /** Lay the same answer out again, for when a person has dragged it into a shape they want to undo. */
  relayout: () => void;
};

export function GraphCanvas({
  nodes,
  edges,
  centerRef,
  selectedRef,
  onSelect,
  onReady,
  interactive = true,
  height,
  fill = false,
}: {
  nodes: GraphNode[];
  edges: GraphEdge[];
  /** The node this answer is about; drawn larger and always labelled. */
  centerRef?: string;
  /** What the person has clicked. Its neighbourhood stays lit while nothing is hovered. */
  selectedRef?: string | null;
  onSelect?: (node: GraphNode) => void;
  /** Handed the camera and layout controls once the picture exists, so the page can offer them. */
  onReady?: (controls: GraphControls | null) => void;
  /** A chat card is a fixed picture of one turn: no panning, no zooming, no dragging. */
  interactive?: boolean;
  height?: number;
  /** Fill the space it is given instead of standing at a fixed height. */
  fill?: boolean;
}) {
  const container = useRef<HTMLDivElement | null>(null);
  const [drawn, setDrawn] = useState(false);
  const select = useRef(onSelect);
  const selected = useRef(selectedRef ?? null);
  const ready = useRef(onReady);
  select.current = onSelect;
  ready.current = onReady;

  // Selection lives outside the effect: choosing a different node re-lights the picture without rebuilding it.
  const refresh = useRef<(() => void) | null>(null);
  useEffect(() => {
    selected.current = selectedRef ?? null;
    refresh.current?.();
  }, [selectedRef]);

  useEffect(() => {
    const element = container.current;
    if (!element) return;
    let cancelled = false;
    let renderer: { kill: () => void } | null = null;
    const observers: ResizeObserver[] = [];

    const draw = (
      Graph: typeof import("graphology").default,
      forceAtlas2: typeof import("graphology-layout-forceatlas2").default,
      Sigma: typeof import("sigma").default,
      EdgeArrowProgram: typeof import("sigma/rendering").EdgeArrowProgram,
    ) => {
      const graph = new Graph({ multi: true, type: "directed" });
      const degree = new Map<string, number>();
      for (const edge of edges) {
        degree.set(edge.from, (degree.get(edge.from) ?? 0) + 1);
        degree.set(edge.to, (degree.get(edge.to) ?? 0) + 1);
      }
      const busiest = Math.max(1, ...degree.values());
      nodes.forEach((node, index) => {
        const ref = refOf(node);
        if (graph.hasNode(ref)) return;
        // A golden-angle spiral start: deterministic for the same answer, and unlike a ring it does not collapse
        // into a wheel when many things hang off one person.
        const angle = index * 2.399963;
        const radius = Math.sqrt(index + 0.5);
        graph.addNode(ref, {
          x: Math.cos(angle) * radius,
          y: Math.sin(angle) * radius,
          // A canvas label is a way to recognise something, not to read it whole: long titles are cut here and stay
          // complete in the detail panel beside the picture.
          // 접어서 담은 것이 있으면 몇인지 이름에 붙인다. 접기는 감추는 것이 아니라 묶는 것이므로, 묶인
          // 개수가 보이지 않으면 사람은 그것이 전부인지 알 수 없다.
          label: shorten(node.kind === "person" ? personName(node.title) : node.title) + (node.folded ? ` ${node.folded}` : ""),
          // Size says how connected something is, so a label goes to what holds the picture together rather than to
          // whichever leaf the grid happened to reach first.
          size: (ref === centerRef ? 10 : 7) + ((degree.get(ref) ?? 0) / busiest) * 8,
          color: nodeColor(node.kind),
          kind: node.kind,
          // Only the centre and the teams claim a label unconditionally; the rest are placed by label density.
          forceLabel: ref === centerRef || node.kind === "team" || node.kind === "project",
        });
      });
      for (const edge of edges) {
        if (!graph.hasNode(edge.from) || !graph.hasNode(edge.to)) continue;
        graph.addEdge(edge.from, edge.to, {
          label: edge.count && edge.count > 1 ? `${edge.label ?? edge.kind} ×${edge.count}` : edge.label ?? edge.kind,
          size: Math.min(3, 1 + (edge.count ?? 1) * 0.4),
          type: "arrow",
          color: token("--scax-graph-edge"),
          forceLabel: false,
        });
      }

      // Lay the graph out before it is ever shown, so nobody watches it settle.
      if (graph.order > 1) {
        forceAtlas2.assign(graph, {
          iterations: graph.order >= 80 ? 150 : 100,
          settings: {
            barnesHutOptimize: graph.order >= 80,
            edgeWeightInfluence: 0.7,
            gravity: 1.05,
            scalingRatio: 8,
            slowDown: 5,
            strongGravityMode: false,
          },
        });
      }

      let hovered: string | null = null;
      const focus = () => hovered ?? selected.current ?? null;
      const neighbourhood = (center: string): Set<string> =>
        graph.hasNode(center) ? new Set([center, ...graph.neighbors(center)]) : new Set();

      let sigma: InstanceType<typeof Sigma>;
      try {
        sigma = new Sigma(graph, element, {
          defaultEdgeType: "arrow",
          edgeProgramClasses: { arrow: EdgeArrowProgram },
          renderEdgeLabels: interactive,
          enableEdgeEvents: false,
          allowInvalidContainer: true,
          labelSize: 11,
          labelWeight: "650",
          labelColor: { color: token("--scax-graph-label") },
          edgeLabelSize: 9,
          edgeLabelWeight: "650",
          edgeLabelColor: { color: token("--scax-graph-edge-label") },
          // Labels are placed by density rather than all at once, so the middle of a busy graph stays readable.
          labelDensity: 0.55,
          labelGridCellSize: 130,
          // Labels appear where there is room for them: the grid keeps them from stacking, and this lets a smaller
          // node still speak when its neighbourhood is not crowded.
          labelRenderedSizeThreshold: 6.5,
          stagePadding: interactive ? 55 : 30,
          minCameraRatio: 0.35,
          maxCameraRatio: 4,
          zIndex: true,
          nodeReducer: (id, data) => {
            const center = focus();
            if (!center) return data;
            if (id === center) return { ...data, highlighted: true, size: data.size * 1.25, zIndex: 2, forceLabel: true };
            if (neighbourhood(center).has(id)) return { ...data, zIndex: 1, forceLabel: true };
            return { ...data, color: DIM_NODE(), label: "", zIndex: 0 };
          },
          edgeReducer: (id, data) => {
            const center = focus();
            if (!center) return { ...data, forceLabel: false };
            const [source, target] = graph.extremities(id);
            const touching = source === center || target === center;
            return touching
              ? { ...data, color: ACTIVE_EDGE(), size: 2.2, forceLabel: true, zIndex: 2 }
              : { ...data, color: DIM_EDGE(), label: "", size: 0.6, forceLabel: false, zIndex: 0 };
          },
        });
      } catch {
        setDrawn(false);
        return null;
      }
      setDrawn(true);
      refresh.current = () => sigma.refresh();
      const camera = sigma.getCamera();
      ready.current?.({
        zoomIn: () => camera.animatedZoom({ duration: 220 }),
        zoomOut: () => camera.animatedUnzoom({ duration: 220 }),
        reset: () => camera.animatedReset({ duration: 260 }),
        relayout: () => {
          forceAtlas2.assign(graph, {
            iterations: graph.order >= 80 ? 150 : 100,
            settings: {
              barnesHutOptimize: graph.order >= 80,
              edgeWeightInfluence: 0.7,
              gravity: 1.05,
              scalingRatio: 8,
              slowDown: 5,
              strongGravityMode: false,
            },
          });
          sigma.setCustomBBox(null);
          camera.animatedReset({ duration: 260 });
          sigma.refresh();
        },
      });

      if (!interactive) {
        // A chat card is a picture of one turn, not a place to explore: nothing pans, zooms or drags.
        sigma.getMouseCaptor().kill();
        sigma.getTouchCaptor().kill();
        return sigma;
      }

      // The card grows to fill the page after this mounts; without being told, Sigma keeps drawing into the size it
      // first saw and the picture sits in a corner.
      const observer = new ResizeObserver(() => {
        sigma.resize();
        sigma.refresh();
      });
      observer.observe(element);
      observers.push(observer);

      sigma.on("enterNode", ({ node }: { node: string }) => {
        hovered = node;
        element.style.cursor = "pointer";
        sigma.refresh();
      });
      sigma.on("leaveNode", () => {
        hovered = null;
        element.style.cursor = "default";
        sigma.refresh();
      });
      sigma.on("clickNode", ({ node }: { node: string }) => {
        const found = nodes.find((candidate) => refOf(candidate) === node);
        if (found) select.current?.(found);
      });

      // Dragging moves where a node sits on screen. The relation it stands for is untouched.
      let dragging: string | null = null;
      sigma.on("downNode", ({ node, event }: { node: string; event: { preventSigmaDefault: () => void } }) => {
        dragging = node;
        graph.setNodeAttribute(node, "highlighted", true);
        if (!sigma.getCustomBBox()) sigma.setCustomBBox(sigma.getBBox());
        event.preventSigmaDefault();
      });
      sigma.getMouseCaptor().on("mousemovebody", (event: never) => {
        if (!dragging) return;
        const moved = event as unknown as { original: Event; x: number; y: number; preventSigmaDefault: () => void };
        graph.mergeNodeAttributes(dragging, sigma.viewportToGraph(moved));
        moved.preventSigmaDefault();
        moved.original.preventDefault();
        moved.original.stopPropagation();
      });
      sigma.getMouseCaptor().on("mouseup", () => {
        if (!dragging) return;
        graph.removeNodeAttribute(dragging, "highlighted");
        dragging = null;
      });
      return sigma;
    };

    // The renderer needs WebGL, so it is loaded only when there is a screen to draw on. Where there is not — a test,
    // a browser refusing a context — the list beside this is the answer, not an error.
    void (async () => {
      const [{ default: Graph }, { default: forceAtlas2 }, { default: Sigma }, { EdgeArrowProgram }] = await Promise.all([
        import("graphology"),
        import("graphology-layout-forceatlas2"),
        import("sigma"),
        import("sigma/rendering"),
      ]);
      if (cancelled || !container.current) return;
      renderer = draw(Graph, forceAtlas2, Sigma, EdgeArrowProgram);
    })().catch(() => setDrawn(false));

    return () => {
      cancelled = true;
      refresh.current = null;
      ready.current?.(null);
      observers.forEach((observer) => observer.disconnect());
      renderer?.kill();
    };
  }, [nodes, edges, centerRef, interactive]);

  return (
    <div className={fill ? "graph-canvas-shell fill" : "graph-canvas-shell"}>
      <div
        aria-hidden={drawn ? undefined : true}
        className="graph-canvas"
        data-drawn={drawn ? "true" : "false"}
        data-node-count={nodes.length}
        ref={container}
        style={height ? { height } : undefined}
      />
      {/* 바퀴 8-B: 구 `.t-meta` 를 이 화면의 새 DS 줄(`.graph-meta`)로 바꿨다.
          ⚠ 이 파일의 `--graph-*` 14종은 한 자도 건드리지 않았다 — sigma 가 getComputedStyle 로
          읽어 가는 값이고 새 DS 에 대응하는 범주형 팔레트가 없다 (G-06). */}
      {!drawn && <p className="graph-meta">이 화면에서는 그림을 그릴 수 없어 아래 목록으로 보여 줍니다.</p>}
    </div>
  );
}
