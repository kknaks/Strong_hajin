import { useState } from "react";

import { graphNeighbors, graphSearch } from "./api";
import type { GraphNeighborhood, GraphNode } from "./viewModels";
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

const KIND_LABEL: Record<string, string> = {
  person: "사람",
  work_request: "업무 요청",
  task: "업무",
  material: "자료",
};

/**
 * 관계 탐색: search for something, then follow one hop at a time.
 *
 * Everything on screen came from the server's authorized answer — work this person may not read is not here, and
 * neither is a count of it. Following a connection is a read, never a way in.
 */
export function RelationGraphPage({
  onOpenTask,
  onError,
}: {
  onOpenTask: (taskId: string) => void;
  onError: (message: string | null) => void;
}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<{ nodes: GraphNode[]; truncated: boolean } | null>(null);
  const [around, setAround] = useState<GraphNeighborhood | null>(null);
  const [busy, setBusy] = useState(false);

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
      setAround(await graphNeighbors(`${node.kind}:${node.id}`));
    } catch (error) {
      onError(error instanceof Error ? error.message : "연결을 불러오지 못했습니다.");
    } finally {
      setBusy(false);
    }
  };

  const nodeOf = (ref: string): GraphNode | undefined => (around?.nodes ?? []).find((row) => `${row.kind}:${row.id}` === ref);
  const centerRef = around ? `${around.center.kind}:${around.center.id}` : "";

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
                    <button aria-label={`${node.title} 중심으로 보기`} className="btn link" onClick={() => void walk(node)} type="button">
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
