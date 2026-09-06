import { useCallback, useEffect, useState } from "react";

import { graphNeighbors, graphOverview, graphSearch } from "./api";
import { GraphCanvas, KIND_LABEL, KIND_SOURCE, refOf } from "./GraphCanvas";
import type { GraphEdge, GraphNeighborhood, GraphNode, GraphOverview } from "./viewModels";
import { personName, taskStateLabel } from "./labels";

/** What a connection means, in the words a person would use rather than the name of the edge. */
const EDGE_SENTENCE: Record<string, { incoming: string; outgoing: string }> = {
  produced: { incoming: "이 업무를 만든 요청", outgoing: "이 요청이 만든 업무" },
  requested: { incoming: "이 요청을 보낸 사람", outgoing: "보낸 요청" },
  asked_of: { incoming: "요청받은 사람", outgoing: "요청받은 사람" },
  holds: { incoming: "담당", outgoing: "담당 중인 업무" },
  parent_of: { incoming: "상위 업무", outgoing: "하위 업무" },
  refers_to: { incoming: "이 업무를 참고한 업무", outgoing: "참고 업무" },
  has_material: { incoming: "붙어 있는 업무", outgoing: "참고 자료·산출물" },
};

const VIEW_LABEL: Record<string, string> = { member: "구성원 보기", team: "팀으로 묶기" };

/**
 * 관계 탐색: search for something, then follow one hop at a time.
 *
 * Everything on screen came from the server's authorized answer — work this person may not read is not here, and
 * neither is a count of it. Following a connection is a read, never a way in.
 */
export function RelationGraphPage({
  onOpenTask,
  onOpenNode,
  onError,
  focusNodeRef,
  onFocusHandled,
}: {
  onOpenTask: (taskId: string) => void;
  /** Open whatever this node stands for, in the surface that owns it. */
  onOpenNode?: (node: GraphNode) => void;
  onError: (message: string | null) => void;
  /** Arriving from a chat card: start centred here, with this person's access applied again. */
  focusNodeRef?: string | null;
  onFocusHandled?: () => void;
}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<{ nodes: GraphNode[]; truncated: boolean } | null>(null);
  const [around, setAround] = useState<GraphNeighborhood | null>(null);
  const [overview, setOverview] = useState<GraphOverview | null>(null);
  const [view, setView] = useState<"member" | "team">("member");
  const [busy, setBusy] = useState(false);
  const [loadingGraph, setLoadingGraph] = useState(true);
  const [selected, setSelected] = useState<GraphNode | null>(null);

  // 첫 화면은 빈 검색 상자가 아니라 지금 이어져 있는 것들이다.
  const loadOverview = useCallback(
    async (next: "member" | "team") => {
      setLoadingGraph(true);
      try {
        setOverview(await graphOverview(next));
        onError(null);
      } catch (error) {
        onError(error instanceof Error ? error.message : "관계를 불러오지 못했습니다.");
      } finally {
        setLoadingGraph(false);
      }
    },
    [onError],
  );

  useEffect(() => {
    void loadOverview(view);
  }, [loadOverview, view]);

  useEffect(() => {
    if (!focusNodeRef) return;
    setBusy(true);
    onError(null);
    void graphNeighbors(focusNodeRef)
      .then(setAround)
      .catch((error: unknown) => onError(error instanceof Error ? error.message : "연결을 불러오지 못했습니다."))
      .finally(() => {
        setBusy(false);
        onFocusHandled?.();
      });
  }, [focusNodeRef, onError, onFocusHandled]);

  const search = async () => {
    const text = query.trim();
    if (!text) {
      onError("무엇을 찾을지 입력해 주세요.");
      return;
    }
    setBusy(true);
    onError(null);
    try {
      const found = await graphSearch(text);
      setResults({ nodes: found.nodes, truncated: found.truncated });
    } catch (error) {
      onError(error instanceof Error ? error.message : "검색하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  };

  const walk = async (node: GraphNode) => {
    setBusy(true);
    onError(null);
    try {
      const neighbourhood = await graphNeighbors(`${node.kind}:${node.id}`);
      setAround(neighbourhood);
      // Centring on something selects it, so the panel reads that node's connections from the answer just loaded.
      setSelected(neighbourhood.nodes.find((row) => refOf(row) === refOf(neighbourhood.center)) ?? neighbourhood.center);
    } catch (error) {
      onError(error instanceof Error ? error.message : "연결을 불러오지 못했습니다.");
    } finally {
      setBusy(false);
    }
  };

  const shown = around ?? overview;
  const nodeOf = (ref: string): GraphNode | undefined => (shown?.nodes ?? []).find((row) => refOf(row) === ref);
  const centerRef = around ? refOf(around.center) : "";

  /** Every connection this node has in the answer on screen, read from its side. */
  const connectionsOf = (node: GraphNode): Array<{ edge: GraphEdge; other: GraphNode; incoming: boolean }> => {
    const ref = refOf(node);
    return (shown?.edges ?? [])
      .filter((edge) => edge.from === ref || edge.to === ref)
      .map((edge) => {
        const incoming = edge.to === ref;
        const other = nodeOf(incoming ? edge.from : edge.to);
        return other ? { edge, other, incoming } : null;
      })
      .filter((row): row is { edge: GraphEdge; other: GraphNode; incoming: boolean } => row !== null);
  };

  const openOriginal = (node: GraphNode) => {
    if (node.kind === "task") {
      onOpenTask(node.id);
      return;
    }
    onOpenNode?.(node);
  };

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>관계 탐색</h1>
          <p>업무가 어떻게 이어져 있는지 한 걸음씩 따라갑니다. 볼 수 있는 것만 보이고, 연결을 따라가도 권한은 늘지 않습니다.</p>
        </div>
      </div>

      <div className="inline-reason" style={{ padding: 0 }}>
        <label className="sr-only" htmlFor="graph-query">
          무엇을 찾을까요
        </label>
        <input
          id="graph-query"
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key !== "Enter") return;
            event.preventDefault();
            if (event.repeat || event.nativeEvent.isComposing) return;
            void search();
          }}
          placeholder="사람 이름이 아니라 업무·요청 제목으로 찾습니다"
          value={query}
        />
        <button className="btn primary" disabled={busy} onClick={() => void search()} type="button">
          찾기
        </button>
      </div>

      <section aria-label="관계 그래프" className="drawer-section">
        <div className="section-row">
          <h4>
            {around ? `${around.center.kind === "person" ? personName(around.center.title) : around.center.title} 중심` : "지금 이어져 있는 것들"}
          </h4>
          <div aria-label="표현 수준" className="segmented" role="tablist">
            {(overview?.available_views ?? ["member", "team"]).map((item) => (
              <button
                aria-selected={view === item}
                disabled={loadingGraph || around !== null}
                key={item}
                onClick={() => setView(item)}
                role="tab"
                type="button"
              >
                {VIEW_LABEL[item] ?? item}
              </button>
            ))}
          </div>
          {around && (
            <button className="btn h30" onClick={() => setAround(null)} type="button">
              첫 화면으로
            </button>
          )}
        </div>
        {around === null && overview === null ? (
          <p className="t-meta">관계를 불러오는 중…</p>
        ) : (shown?.nodes.length ?? 0) === 0 ? (
          <div className="empty-state">
            <b>아직 이어진 것이 없습니다</b>
            <p>업무를 맡거나 요청을 주고받으면 여기에서 이어집니다. 볼 수 있는 범위 안에서만 그립니다.</p>
          </div>
        ) : (
          <div className="graph-with-detail">
            <GraphCanvas
              centerRef={centerRef || undefined}
              edges={shown?.edges ?? []}
              nodes={shown?.nodes ?? []}
              onSelect={setSelected}
              selectedRef={selected ? refOf(selected) : null}
            />
            <aside aria-label="선택한 노드" className="graph-detail">
              {selected === null ? (
                <p className="t-meta">
                  노드에 마우스를 올리면 그 지점의 직접 연결만 밝아집니다. 눌러서 고르면 여기에서 정본과 연결을 봅니다.
                </p>
              ) : (
                <>
                  <div className="graph-detail-head">
                    <span className="ax-resource-kind">{KIND_LABEL[selected.kind] ?? selected.kind}</span>
                    <b>{selected.kind === "person" ? personName(selected.title) : selected.title}</b>
                    {selected.state && (
                      <span className="t-meta">
                        {taskStateLabel[selected.state as keyof typeof taskStateLabel] ?? selected.state}
                      </span>
                    )}
                  </div>
                  <dl className="meta-grid">
                    <div>
                      <dt>정본</dt>
                      <dd>{KIND_SOURCE[selected.kind] ?? selected.kind}</dd>
                    </div>
                  </dl>
                  <h5 className="t-meta">직접 연결 {connectionsOf(selected).length}개</h5>
                  <ul className="graph-relations">
                    {connectionsOf(selected).map(({ edge, other, incoming }) => (
                      <li data-relation={edge.kind} key={`${edge.kind}:${edge.from}:${edge.to}`}>
                        <button className="btn link" onClick={() => setSelected(other)} type="button">
                          {other.kind === "person" ? personName(other.title) : other.title}
                        </button>
                        <span className="t-meta">
                          {(incoming ? edge.inverse_label : edge.label) ?? edge.kind}
                          {edge.count && edge.count > 1 ? ` ×${edge.count}` : ""} · 출처 {edge.provenance ?? edge.kind}
                        </span>
                      </li>
                    ))}
                  </ul>
                  <div className="graph-detail-actions">
                    <button className="btn h30" disabled={busy} onClick={() => void walk(selected)} type="button">
                      이 지점 중심으로
                    </button>
                    {(selected.kind === "task" || onOpenNode) && (
                      <button className="btn h30 primary" onClick={() => openOriginal(selected)} type="button">
                        {KIND_LABEL[selected.kind] ?? "원본"} 상세 열기
                      </button>
                    )}
                  </div>
                </>
              )}
            </aside>
          </div>
        )}
        <p className="t-meta">
          {shown?.truncated
            ? "연결이 더 있습니다. 한 번에 보여 주는 수를 넘었습니다 — 한 지점을 중심으로 두면 그 주변을 다 볼 수 있습니다."
            : "노드를 누르면 오른쪽에서 정본과 연결을 봅니다. 볼 수 있는 것만 그려지고, 따라가도 권한은 늘지 않습니다."}
        </p>
      </section>

      <div className="graph-layout">
        <section aria-label="검색 결과" className="drawer-section">
          <h4>검색 결과</h4>
          {results === null ? (
            <p className="t-meta">무엇부터 볼지 찾아 주세요.</p>
          ) : results.nodes.length === 0 ? (
            <p className="t-meta">찾은 것이 없습니다. 볼 수 있는 범위 안에서만 찾습니다.</p>
          ) : (
            <>
              <ul className="material-list">
                {results.nodes.map((node) => (
                  <li data-node={`${node.kind}:${node.id}`} key={`${node.kind}:${node.id}`}>
                    <button
                      aria-label={`${node.title} 중심으로 보기`}
                      className="btn link"
                      onClick={() => void walk(node)}
                      type="button"
                    >
                      {node.title}
                    </button>
                    <span className="t-meta">
                      {KIND_LABEL[node.kind] ?? node.kind}
                      {node.state ? ` · ${taskStateLabel[node.state as keyof typeof taskStateLabel] ?? node.state}` : ""}
                    </span>
                  </li>
                ))}
              </ul>
              {results.truncated && <p className="t-meta">여기 보이는 것보다 더 있습니다. 더 좁혀서 찾아 주세요.</p>}
            </>
          )}
        </section>

        <section aria-label="연결" className="drawer-section">
          {around === null ? (
            <p className="t-meta">하나를 골라 중심으로 두면 그 한 걸음의 연결을 봅니다.</p>
          ) : (
            <>
              <div className="section-row">
                <h4>
                  {around.center.title} <span className="t-meta">· {KIND_LABEL[around.center.kind] ?? around.center.kind}</span>
                </h4>
                {around.center.kind === "task" && (
                  <button className="btn h30" onClick={() => onOpenTask(around.center.id)} type="button">
                    원본 업무 열기
                  </button>
                )}
              </div>
              {around.edges.length === 0 ? (
                <p className="t-meta">이어진 것이 없습니다.</p>
              ) : (
                <ul className="material-list">
                  {around.edges.map((edge) => {
                    const incoming = edge.to === centerRef;
                    const other = nodeOf(incoming ? edge.from : edge.to);
                    if (!other) return null;
                    const sentence = EDGE_SENTENCE[edge.kind] ?? { incoming: edge.kind, outgoing: edge.kind };
                    return (
                      <li data-edge={edge.kind} data-node={`${other.kind}:${other.id}`} key={`${edge.kind}:${edge.from}:${edge.to}`}>
                        <span className="t-meta">{incoming ? sentence.incoming : sentence.outgoing}</span>
                        <button aria-label={`${other.title} 열기`} className="btn link" onClick={() => void walk(other)} type="button">
                          {other.kind === "person" ? personName(other.title) : other.title}
                        </button>
                        <span className="t-meta">
                          {KIND_LABEL[other.kind] ?? other.kind}
                          {other.state ? ` · ${taskStateLabel[other.state as keyof typeof taskStateLabel] ?? other.state}` : ""}
                        </span>
                      </li>
                    );
                  })}
                </ul>
              )}
              {around.truncated && <p className="t-meta">연결이 더 있습니다. 한 번에 보여 주는 수를 넘었습니다.</p>}
            </>
          )}
        </section>
      </div>
    </div>
  );
}
