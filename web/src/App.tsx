import { useCallback, useEffect, useMemo, useState } from "react";

type Persona = { id: string; display_name: string };
type GraphNode = { id: string; kind: string; label: string };
type GraphEdge = { source: string; target: string };
type Workflow = {
  workflow_id: string;
  version: string;
  title: string;
  description: string;
  input_schema: { required?: string[]; properties?: Record<string, unknown>; additionalProperties?: boolean };
  graph: { nodes: GraphNode[]; edges: GraphEdge[] };
};
type ToolResult = { tool_name: string; result: Record<string, unknown> };
type AuditEvent = { event_type: string; payload: Record<string, unknown> };
type Run = {
  run_id: string;
  workflow_id: string;
  definition_version: string;
  state: string;
  waiting_on: string[];
  tool_results: ToolResult[];
  audit: AuditEvent[];
};
type InboxItem = { run_id: string; workflow_id: string; node_id: string; label: string };
type Assignment = { assignment_id: string; title: string; state: string; run_id: string };

const api = async <T,>(path: string, persona: string, init: RequestInit = {}): Promise<T> => {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", "X-Demo-Persona": persona, ...init.headers },
  });
  if (!response.ok) throw new Error((await response.json().catch(() => ({ detail: response.statusText }))).detail);
  return response.json() as Promise<T>;
};

const stateLabel: Record<string, string> = {
  waiting_for_decision: "판단 대기",
  running: "진행 중",
  completed: "완료",
  rejected: "반려됨",
};

export default function App() {
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [persona, setPersona] = useState("mina");
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [inbox, setInbox] = useState<InboxItem[]>([]);
  const [myWork, setMyWork] = useState<Assignment[]>([]);
  const [run, setRun] = useState<Run | null>(null);
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [catalog, nextInbox, nextMyWork] = await Promise.all([
        api<Workflow[]>("/api/catalog", persona),
        api<InboxItem[]>("/api/inbox", persona),
        api<Assignment[]>("/api/my-work", persona),
      ]);
      setWorkflows(catalog);
      setInbox(nextInbox);
      setMyWork(nextMyWork);
      if (run) {
        try {
          setRun(await api<Run>(`/api/runs/${run.run_id}`, persona));
        } catch {
          setRun(null);
        }
      }
      setError(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "데이터를 불러오지 못했습니다.");
    }
  }, [persona, run]);

  useEffect(() => {
    fetch("/api/developer/personas")
      .then((response) => response.json() as Promise<Persona[]>)
      .then(setPersonas)
      .catch(() => setError("개발자 로그인 정보를 불러오지 못했습니다."));
  }, []);

  useEffect(() => {
    void refresh();
  }, [persona]); // eslint-disable-line react-hooks/exhaustive-deps

  const selected = useMemo(() => workflows.find((workflow) => workflow.workflow_id === run?.workflow_id), [workflows, run]);

  const start = async (workflow: Workflow) => {
    setBusy(workflow.workflow_id);
    try {
      const required = workflow.input_schema.required ?? [];
      const input = Object.fromEntries(required.map((field) => [field, inputs[`${workflow.workflow_id}:${field}`] ?? ""]));
      if (required.some((field) => !input[field])) throw new Error(`${required.join(", ")} 입력이 필요합니다.`);
      setRun(await api<Run>(`/api/runs/${workflow.workflow_id}`, persona, { method: "POST", body: JSON.stringify({ input }) }));
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Workflow를 시작하지 못했습니다.");
    } finally {
      setBusy(null);
    }
  };

  const decide = async (item: InboxItem, decision: "accept" | "reject") => {
    setBusy(`${item.run_id}:${item.node_id}`);
    try {
      const payload = item.node_id === "choose-assignment" ? { assignee_id: "mina" } : {};
      const updated = await api<Run>(`/api/runs/${item.run_id}/decisions/${item.node_id}`, persona, {
        method: "POST",
        body: JSON.stringify({ decision, payload }),
      });
      setRun(updated);
      await refresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "판단을 저장하지 못했습니다.");
    } finally {
      setBusy(null);
    }
  };

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand"><span className="brand-mark">S</span><span>SCAX</span><span className="quiet">Workflow Catalog</span></div>
        <label className="persona-picker">데모 사용자
          <select value={persona} onChange={(event) => setPersona(event.target.value)}>
            {personas.map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}
          </select>
        </label>
      </header>

      <section className="hero">
        <div><p className="eyebrow">VERSION-PINNED WORKFLOW RUNTIME</p><h1>내 권한으로 시작하는<br />업무 흐름</h1><p>Catalog, Tool 실행, 사람 판단과 Audit을 하나의 runtime에서 확인합니다.</p></div>
        <div className="hero-metrics"><strong>{workflows.length}</strong><span>허용된 Workflow</span><strong>{inbox.length}</strong><span>내 판단 Inbox</span></div>
      </section>

      {error && <div className="error-banner" role="alert">{error}<button onClick={() => setError(null)}>닫기</button></div>}

      <div className="workspace-grid">
        <section className="panel catalog-panel"><div className="panel-heading"><div><p className="eyebrow">CATALOG</p><h2>시작할 Workflow</h2></div><button className="ghost" onClick={() => void refresh()}>새로고침</button></div>
          <div className="workflow-list">{workflows.map((workflow) => <article className="workflow-card" key={workflow.workflow_id}>
            <div><span className="version">{workflow.version}</span><h3>{workflow.title}</h3><p>{workflow.description}</p>{(workflow.input_schema.required ?? []).map((field) => <label className="inline-input" key={field}>{field}<input value={inputs[`${workflow.workflow_id}:${field}`] ?? ""} placeholder={`${field} 입력`} onChange={(event) => setInputs({ ...inputs, [`${workflow.workflow_id}:${field}`]: event.target.value })} /></label>)}</div>
            <button className="primary" disabled={busy !== null} onClick={() => void start(workflow)}>{busy === workflow.workflow_id ? "시작 중" : "실행"}</button>
          </article>)}</div>
        </section>

        <section className="panel run-panel"><div className="panel-heading"><div><p className="eyebrow">RUN PROGRESS</p><h2>{selected?.title ?? "실행을 선택하세요"}</h2></div>{run && <span className={`status ${run.state}`}>{stateLabel[run.state] ?? run.state}</span>}</div>
          {!run ? <div className="empty"><span>◎</span><p>Catalog에서 실행을 시작하면 version, graph, Tool 결과와 Audit이 여기에 표시됩니다.</p></div> : <>
            <div className="run-meta"><code>{run.run_id}</code><span>definition {run.definition_version}</span></div>
            <ol className="graph">{selected?.graph.nodes.map((node) => <li className={run.waiting_on.includes(node.id) ? "waiting" : ""} key={node.id}><span>{node.kind === "human_gate" ? "◇" : node.kind === "tool" ? "○" : "●"}</span><div><b>{node.label}</b><small>{node.kind}</small></div></li>)}</ol>
            <div className="detail-grid"><div><h3>Tool results</h3>{run.tool_results.map((item) => <pre key={item.tool_name}>{item.tool_name}{"\n"}{JSON.stringify(item.result, null, 2)}</pre>)}</div><div><h3>Audit</h3><ul className="audit">{run.audit.map((item, index) => <li key={`${item.event_type}-${index}`}>{item.event_type}</li>)}</ul></div></div>
          </>}
        </section>

        <aside className="panel inbox-panel"><div className="panel-heading"><div><p className="eyebrow">HUMAN INBOX</p><h2>내 판단</h2></div><span className="count">{inbox.length}</span></div>
          {inbox.length === 0 ? <div className="empty compact"><span>✓</span><p>지금 처리할 판단이 없습니다.</p></div> : inbox.map((item) => <article className="inbox-card" key={`${item.run_id}:${item.node_id}`}><span className="workflow-tag">{item.workflow_id}</span><h3>{item.label}</h3><p>{item.node_id === "choose-assignment" ? "민아에게 수행자 수락을 요청합니다." : "증거를 검토한 뒤 결정하세요."}</p><div className="actions"><button className="secondary" disabled={busy !== null} onClick={() => void decide(item, "reject")}>반려</button><button className="primary" disabled={busy !== null} onClick={() => void decide(item, "accept")}>{busy === `${item.run_id}:${item.node_id}` ? "저장 중" : "수락"}</button></div></article>)}
          <div className="my-work"><div className="panel-heading"><div><p className="eyebrow">MY WORK</p><h2>내 업무</h2></div><span className="count muted">{myWork.length}</span></div>{myWork.length === 0 ? <p className="my-work-empty">수락 후 활성화된 업무가 표시됩니다.</p> : myWork.map((assignment) => <article className="assignment-card" key={assignment.assignment_id}><span className="workflow-tag">{assignment.state}</span><h3>{assignment.title}</h3><p>수락된 TaskAssignment</p></article>)}</div>
        </aside>
      </div>
    </main>
  );
}
