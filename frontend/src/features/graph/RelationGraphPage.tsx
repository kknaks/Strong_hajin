import { useCallback, useEffect, useMemo, useState } from "react";

import { Button } from "../../ds/Button";
import { SegmentedControl } from "../../ds/SegmentedControl";
import { graphNeighbors, graphOverview, graphSearch } from "../../lib/api";
import { EDGE_SENTENCE, GraphCanvas, KIND_COLOR, KIND_LABEL, KIND_SOURCE, refOf, type GraphControls } from "./GraphCanvas";
import type { GraphEdge, GraphNeighborhood, GraphNode, GraphOverview, GraphView } from "../../lib/viewModels";
import { emptyActionLabel, personName, taskStateLabel, workRequestStateLabel } from "../../lib/labels";
import { Chip } from "../../ds/Chip";
import { Empty } from "../../ds/Empty";
import { Icon } from "../../ds/icons/Icon";

/** What a connection means, in the words a person would use rather than the name of the edge. */
const VIEW_LABEL: Record<string, string> = { member: "구성원 보기", team: "팀으로 묶기", project: "프로젝트로 묶기" };

/** A node's state in the words its own ledger uses — a task's state, a request's, a meeting's visibility. */
const OTHER_STATE_LABEL: Record<string, string> = { private: "비공개 회의", public: "조직 공개 회의", input: "참고 자료", output: "산출물" };
const stateText = (state: string): string =>
  taskStateLabel[state as keyof typeof taskStateLabel] ??
  workRequestStateLabel[state as keyof typeof workRequestStateLabel] ??
  OTHER_STATE_LABEL[state] ??
  state;

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
  const [view, setView] = useState<GraphView>("member");
  const [busy, setBusy] = useState(false);
  const [loadingGraph, setLoadingGraph] = useState(true);
  const [selected, setSelected] = useState<GraphNode | null>(null);
  const [hidden, setHidden] = useState<Set<GraphNode["kind"]>>(new Set());
  const [controls, setControls] = useState<GraphControls | null>(null);

  // 첫 화면은 빈 검색 상자가 아니라 지금 이어져 있는 것들이다.
  const loadOverview = useCallback(
    async (next: GraphView) => {
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

  const kinds = useMemo(() => {
    const present = new Set((shown?.nodes ?? []).map((node) => node.kind));
    return (Object.keys(KIND_LABEL) as GraphNode["kind"][]).filter((kind) => present.has(kind));
  }, [shown]);

  /**
   * 표시할 node 종류를 고르는 것은 보는 방법이지 다른 질문이 아니다. 같은 인가된 답을 덜 그릴 뿐이고 서버에
   * 새로 묻는 것이 없다.
   *
   * 상태·기간 같은 범용 조건은 여기 두지 않는다. 그것은 개발자용 query builder에 가깝고, 질문에 맞는 타입별
   * 조건은 Tool이 내부 계약으로 가진다. 각 제품 화면의 고유 목록 filter는 그 화면이 따로 갖는다.
   */
  const visible = useMemo(() => {
    if (!shown) return null;
    if (hidden.size === 0) return shown;
    const nodes = shown.nodes.filter((node) => !hidden.has(node.kind));
    const kept = new Set(nodes.map((node) => refOf(node)));
    return { ...shown, nodes, edges: shown.edges.filter((edge) => kept.has(edge.from) && kept.has(edge.to)) };
  }, [hidden, shown]);

  return (
    <div className="page graph-page">
      {/* 바퀴 8-B: 페이지 h1 「관계 탐색」을 걷어냈다 — 셸 머리(.scax-page-header > h1)가 같은 글자를
          이미 이고 있었다(바퀴 5a J-2 와 같은 처리). 설명 문단은 값이라 남긴다. */}
      <div className="screens-b-lead">
        <p className="screens-b-lead__text">
          업무가 어떻게 이어져 있는지 한 걸음씩 따라갑니다. 볼 수 있는 것만 보이고, 연결을 따라가도 권한은 늘지 않습니다.
        </p>
      </div>

      <div className="graph-toolbar">
        <SegmentedControl
          ariaLabel="표현 수준"
          onChange={setView}
          options={(overview?.available_views ?? ["member", "team"]).map((item) => ({
            value: item,
            label: VIEW_LABEL[item] ?? item,
            disabled: loadingGraph || around !== null,
          }))}
          value={view}
        />
        <label className="sr-only" htmlFor="graph-query">
          무엇을 찾을까요
        </label>
        {/* 바퀴 8-B: 맨 클래스 `.graph-search` 이던 검색칸을 새 DS 텍스트 필드 골격으로 옮겼다 */}
        <div className="scax-textfield graph-search-field">
          <input
            className="scax-textfield__input"
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
        </div>
        <Button variant="solid" tone="primary" size="sm" disabled={busy} onClick={() => void search()} type="button">
          찾기
        </Button>
        {around && (
          <Button onClick={() => { setAround(null); setSelected(null); }} type="button">
            첫 화면으로
          </Button>
        )}
        <span className="spacer" />
        <span className="renderer-badge">Sigma.js 3 · Graphology · ForceAtlas2</span>
      </div>

      <section aria-label="관계 그래프" className="graph-card">
        {around === null && overview === null ? (
          <p className="graph-meta graph-card-empty">관계를 불러오는 중…</p>
        ) : (visible?.nodes.length ?? 0) === 0 ? (
          /* 바퀴 11: 「아무것도 없다」와 「내가 숨겼다」는 다른 상태다 — 전자에는 되돌릴 행동이 없고
             후자에는 있다. 예전에는 onAction 하나를 조건부로 넘겨 한 덩어리로 썼는데, 행동과 그 이름이
             한 쌍이 되면서 둘로 갈랐다. 그리는 것은 예전과 같다. */
          (shown?.nodes.length ?? 0) === 0 ? (
            <Empty
              className="graph-card-empty"
              description="업무를 맡거나 요청을 주고받으면 여기에서 이어집니다. 볼 수 있는 범위 안에서만 그립니다."
              title="아직 이어진 것이 없습니다"
              variant="default"
            />
          ) : (
            <Empty
              actionLabel={emptyActionLabel.filter}
              className="graph-card-empty"
              description="위의 종류 단추를 다시 눌러 보여 주세요."
              onAction={() => setHidden(new Set())}
              title="고른 종류가 모두 숨겨져 있습니다"
              variant="filter"
            />
          )
        ) : (
          <>
            <GraphCanvas
              centerRef={centerRef || undefined}
              edges={visible?.edges ?? []}
              fill
              nodes={visible?.nodes ?? []}
              onReady={setControls}
              onSelect={setSelected}
              selectedRef={selected ? refOf(selected) : null}
            />

            <div className="graph-overlay top">
              <span className="graph-pill">
                내 권한으로 접근 가능한 연결 · {around ? "한 걸음 이웃" : "첫 화면"} · {VIEW_LABEL[view]}
              </span>
              <span className="graph-pill">
                {visible?.nodes.length ?? 0}개 노드 · {visible?.edges.length ?? 0}개 연결
                {shown?.truncated ? " · 더 있음" : ""}
              </span>
              {around && (
                <span className="graph-pill">
                  중심 · {around.center.kind === "person" ? personName(around.center.title) : around.center.title}
                </span>
              )}
            </div>

            <div aria-label="좁혀 보기" className="graph-overlay filters" role="group">
              {/* 바퀴 8-B: 맨 클래스 `.graph-filter` 이던 토글을 새 DS 의 Chip 부품으로 옮겼다.
                  ⚠ 색점만은 `--graph-node-*` 를 그대로 받는다 — 새 DS 에 범주형 팔레트가 없어서
                  대신 쓸 색이 없다. 캔버스가 읽는 값과 같아야 범례가 그림과 맞는다 (G-06). */}
              {kinds.map((kind) => (
                <Chip
                  key={kind}
                  on={!hidden.has(kind)}
                  onClick={() =>
                    setHidden((current) => {
                      const next = new Set(current);
                      if (next.has(kind)) next.delete(kind);
                      else next.add(kind);
                      return next;
                    })
                  }
                  type="button"
                >
                  <i className="graph-kind-dot" style={{ background: KIND_COLOR[kind] }} />
                  {KIND_LABEL[kind]}
                </Chip>
              ))}
            </div>

            <div aria-hidden className="graph-overlay legend">
              {kinds.map((kind) => (
                <span key={kind}>
                  <i style={{ background: KIND_COLOR[kind] }} /> {KIND_LABEL[kind]}
                </span>
              ))}
            </div>
            <p aria-hidden className="graph-overlay hint">
              스크롤 확대 · 배경 이동 · 노드 드래그 · 올리면 이웃만 밝아짐 · 누르면 정본과 연결
            </p>

            <div aria-label="화면 조작" className="graph-overlay zoom" role="group">
              {/* 바퀴 8-B: 네 단추를 새 DS 의 `.scax-icon-button` 으로 옮겼다. 세로로 붙어 서는
                  줄만 화면 CSS 가 준다 (screens-8b.css). */}
              <button aria-label="확대" className="scax-icon-button" disabled={!controls} onClick={() => controls?.zoomIn()} type="button">
                <Icon name="plus" size={14} />
              </button>
              <button aria-label="축소" className="scax-icon-button" disabled={!controls} onClick={() => controls?.zoomOut()} type="button">
                <Icon name="minus" size={14} />
              </button>
              <button aria-label="전체 보기" className="scax-icon-button" disabled={!controls} onClick={() => controls?.reset()} type="button">
                <Icon name="home" size={14} />
              </button>
              <button aria-label="배치 다시 계산" className="scax-icon-button" disabled={!controls} onClick={() => controls?.relayout()} type="button">
                <Icon name="reset" size={14} />
              </button>
            </div>

            {selected && (
              <aside aria-label="선택한 노드" className="graph-detail">
                <div className="graph-detail-head">
                  <span className="ax-resource-kind">{KIND_LABEL[selected.kind] ?? selected.kind}</span>
                  <b className="graph-detail-head__name">{selected.kind === "person" ? personName(selected.title) : selected.title}</b>
                  {selected.state && <span className="graph-meta">{stateText(selected.state)}</span>}
                  <button
                    aria-label="상세 닫기"
                    className="scax-icon-button graph-detail-close"
                    onClick={() => setSelected(null)}
                    type="button"
                  >
                    <Icon name="close" size={14} />
                  </button>
                </div>
                <dl className="meta-grid">
                  <div>
                    <dt>정본</dt>
                    <dd>{KIND_SOURCE[selected.kind] ?? selected.kind}</dd>
                  </div>
                </dl>
                <h5 className="graph-meta">직접 연결 {connectionsOf(selected).length}개</h5>
                <ul className="graph-relations">
                  {connectionsOf(selected).map(({ edge, other, incoming }) => (
                    <li data-relation={edge.kind} key={`${edge.kind}:${edge.from}:${edge.to}`}>
                      <Button variant="text" size="sm" onClick={() => setSelected(other)} type="button">
                        {other.kind === "person" ? personName(other.title) : other.title}
                      </Button>
                      <span className="graph-meta">
                        {(incoming ? edge.inverse_label : edge.label) ?? edge.kind}
                        {edge.count && edge.count > 1 ? ` ×${edge.count}` : ""} · 출처 {edge.provenance ?? edge.kind}
                      </span>
                    </li>
                  ))}
                </ul>
                <div className="graph-detail-actions">
                  <Button size="sm" disabled={busy} onClick={() => void walk(selected)} type="button">
                    이 지점 중심으로
                  </Button>
                  {(selected.kind === "task" || onOpenNode) && (
                    <Button variant="solid" tone="primary" size="sm" onClick={() => openOriginal(selected)} type="button">
                      {KIND_LABEL[selected.kind] ?? "원본"} 상세 열기
                    </Button>
                  )}
                </div>
              </aside>
            )}
          </>
        )}
      </section>

      <div className="graph-layout">
        <section aria-label="검색 결과" className="drawer-section">
          <h4>검색 결과</h4>
          {results === null ? (
            <p className="graph-meta">무엇부터 볼지 찾아 주세요.</p>
          ) : results.nodes.length === 0 ? (
            <p className="graph-meta">찾은 것이 없습니다. 볼 수 있는 범위 안에서만 찾습니다.</p>
          ) : (
            <>
              <ul className="material-list">
                {results.nodes.map((node) => (
                  <li data-node={`${node.kind}:${node.id}`} key={`${node.kind}:${node.id}`}>
                    <Button aria-label={`${node.title} 중심으로 보기`} variant="text" size="sm" onClick={() => void walk(node)} type="button">
                      {node.title}
                    </Button>
                    <span className="graph-meta">
                      {KIND_LABEL[node.kind] ?? node.kind}
                      {node.state ? ` · ${taskStateLabel[node.state as keyof typeof taskStateLabel] ?? node.state}` : ""}
                    </span>
                  </li>
                ))}
              </ul>
              {results.truncated && <p className="graph-meta">여기 보이는 것보다 더 있습니다. 더 좁혀서 찾아 주세요.</p>}
            </>
          )}
        </section>

        <section aria-label="연결" className="drawer-section">
          {around === null ? (
            <p className="graph-meta">하나를 골라 중심으로 두면 그 한 걸음의 연결을 봅니다.</p>
          ) : (
            <>
              <div className="section-row">
                <h4>
                  {around.center.title} <span className="graph-meta">· {KIND_LABEL[around.center.kind] ?? around.center.kind}</span>
                </h4>
                {around.center.kind === "task" && (
                  <Button size="sm" onClick={() => onOpenTask(around.center.id)} type="button">
                    원본 업무 열기
                  </Button>
                )}
              </div>
              {around.edges.length === 0 ? (
                <p className="graph-meta">이어진 것이 없습니다.</p>
              ) : (
                <ul className="material-list">
                  {around.edges.map((edge) => {
                    const incoming = edge.to === centerRef;
                    const other = nodeOf(incoming ? edge.from : edge.to);
                    if (!other) return null;
                    const sentence = EDGE_SENTENCE[edge.kind] ?? { incoming: edge.kind, outgoing: edge.kind };
                    return (
                      <li data-edge={edge.kind} data-node={`${other.kind}:${other.id}`} key={`${edge.kind}:${edge.from}:${edge.to}`}>
                        <span className="graph-meta">{incoming ? sentence.incoming : sentence.outgoing}</span>
                        <Button aria-label={`${other.title} 열기`} variant="text" size="sm" onClick={() => void walk(other)} type="button">
                          {other.kind === "person" ? personName(other.title) : other.title}
                        </Button>
                        <span className="graph-meta">
                          {KIND_LABEL[other.kind] ?? other.kind}
                          {other.state ? ` · ${taskStateLabel[other.state as keyof typeof taskStateLabel] ?? other.state}` : ""}
                        </span>
                      </li>
                    );
                  })}
                </ul>
              )}
              {around.truncated && <p className="graph-meta">연결이 더 있습니다. 한 번에 보여 주는 수를 넘었습니다.</p>}
            </>
          )}
        </section>
      </div>
    </div>
  );
}
