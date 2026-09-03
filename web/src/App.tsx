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

const auditLabel: Record<string, string> = {
  "workflow_run.started": "업무가 시작되었습니다.",
  "workflow_node.started": "업무 단계가 시작되었습니다.",
  "workflow_node.completed": "업무 단계가 완료되었습니다.",
  "human_decision.requested": "확인이 필요한 요청이 도착했습니다.",
  "human_decision.accepted": "요청이 승인되었습니다.",
  "human_decision.rejected": "요청이 반려되었습니다.",
  "workflow_run.completed": "업무가 완료되었습니다.",
  "workflow_run.rejected": "업무가 종료되었습니다.",
};

const resultLabel: Record<string, string> = {
  "work_record.lookup": "업무 기록을 확인했습니다.",
  "daily_report.submit_snapshot": "보고 내용을 저장했습니다.",
  "meeting_evidence.lookup": "회의 기록을 확인했습니다.",
  "task_assignment.request": "담당자 확인을 요청했습니다.",
  "task_assignment.activate": "업무를 내 업무에 반영했습니다.",
  "contract.extract": "계약 정보를 확인했습니다.",
  "contract.approve": "계약 검토를 완료했습니다.",
};

export default function App() {
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [persona, setPersona] = useState("mina");
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [inbox, setInbox] = useState<InboxItem[]>([]);
  const [myWork, setMyWork] = useState<Assignment[]>([]);
  const [assignmentCandidates, setAssignmentCandidates] = useState<Persona[]>([]);
  const [assigneeId, setAssigneeId] = useState("mina");
  const [run, setRun] = useState<Run | null>(null);
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [surface, setSurface] = useState<"today" | "work" | "report" | "calendar" | "materials" | "org" | "settings">("today");

  const refresh = useCallback(async () => {
    try {
      const [catalog, nextInbox, nextMyWork, candidates] = await Promise.all([
        api<Workflow[]>("/api/catalog", persona),
        api<InboxItem[]>("/api/inbox", persona),
        api<Assignment[]>("/api/my-work", persona),
        api<Persona[]>("/api/meeting-assignment-candidates", persona).catch(() => []),
      ]);
      setWorkflows(catalog);
      setInbox(nextInbox);
      setMyWork(nextMyWork);
      setAssignmentCandidates(candidates);
      if (candidates.length && !candidates.some((candidate) => candidate.id === assigneeId)) {
        setAssigneeId(candidates[0].id);
      }
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
  }, [persona, run, assigneeId]);

  useEffect(() => {
    fetch("/api/developer/personas")
      .then(async (response) => {
        if (!response.ok) throw new Error(`개발자 로그인 정보를 불러오지 못했습니다 (${response.status}).`);
        const result: unknown = await response.json();
        if (!Array.isArray(result)) throw new Error("개발자 로그인 정보 형식이 올바르지 않습니다.");
        return (result as Persona[]).filter((item) => item.id !== "demo-admin");
      })
      .then((availablePersonas) => {
        setPersonas(availablePersonas);
        if (!availablePersonas.some((item) => item.id === persona)) setPersona(availablePersonas[0]?.id ?? "");
      })
      .catch(() => setError("개발자 로그인 정보를 불러오지 못했습니다."));
  }, [persona]);

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
      const payload = item.node_id === "choose-assignment" ? { assignee_id: assigneeId } : {};
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

  const currentPersona = personas.find((item) => item.id === persona)?.display_name ?? "사용자";
  const navigation = [
    ["today", "오늘"], ["calendar", "캘린더"], ["work", "내 업무"], ["report", "보고"],
    ["materials", "자료"], ["org", "조직"], ["settings", "설정"],
  ] as const;
  const visibleWorkflows = surface === "report" ? workflows.filter((workflow) => workflow.workflow_id.includes("report")) : workflows;

  return <main className="thesc-shell">
    <aside className="rail"><div className="wordmark"><span className="wordmark-mark" />SCAX AX</div><div className="profile"><span className="avatar">{currentPersona.slice(0, 1)}</span><div><b>{currentPersona}</b><small>워크스페이스</small></div></div><nav>{navigation.map(([id, label]) => <button key={id} className={surface === id ? "active" : ""} onClick={() => { setSurface(id); setRun(null); }}>{label}</button>)}</nav></aside>
    <section className="canvas">
      <header className="canvas-topbar"><div><button className="date-chip" onClick={() => void refresh()}>2026년 9월 3일 · 새로고침</button></div><div className="top-actions"><label className="persona-picker">사용자<select value={persona} onChange={(event) => setPersona(event.target.value)}>{personas.map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select></label><button className="new-work" onClick={() => setSurface("work")}>새 업무 추가</button></div></header>
      {error && <div className="error-banner" role="alert">{error}<button onClick={() => setError(null)}>닫기</button></div>}
      <section className="welcome"><span className="avatar large">{currentPersona.slice(0, 1)}</span><h1>반갑습니다 {currentPersona}님!</h1><p>오늘의 업무를 시작해볼까요?</p><button className="ask-bar" onClick={() => setSurface("work")}><span>✦</span> 업무 흐름을 시작하거나 현재 진행을 확인하세요 <b>→</b></button></section>
      {surface === "today" && <>
        <div className="dashboard-columns">
          <section className="surface-card requests"><div className="card-title"><h2>오늘 나에게 요청된 업무</h2><button onClick={() => setSurface("work")}>전체보기</button></div><div className="table-head"><span>내용</span><span>상태</span><span>요청 Workflow</span><span>판단</span></div>{inbox.length === 0 ? <p className="empty-row">지금 처리할 판단이 없습니다.</p> : inbox.map((item) => <article className="request-row" key={`${item.run_id}:${item.node_id}`}><b>{item.label}</b><span className="status waiting_for_decision">승인대기</span><small>{item.workflow_id}</small><div className="row-actions">{item.node_id === "choose-assignment" && <select value={assigneeId} onChange={(event) => setAssigneeId(event.target.value)}>{assignmentCandidates.map((candidate) => <option key={candidate.id} value={candidate.id}>{candidate.display_name}</option>)}</select>}<button className="outline" disabled={busy !== null} onClick={() => void decide(item, "reject")}>검토하기</button><button className="primary" disabled={busy !== null || (item.node_id === "choose-assignment" && assignmentCandidates.length === 0)} onClick={() => void decide(item, "accept")}>{busy === `${item.run_id}:${item.node_id}` ? "저장 중" : "승인"}</button></div></article>)}</section>
          <section className="surface-card progress-card"><div className="card-title"><h2>오늘 이어서 진행하는 업무</h2><button onClick={() => setSurface("work")}>전체보기</button></div><div className="work-tabs"><b>내 업무 {myWork.length}</b><span>참조 업무</span></div>{myWork.length === 0 ? <p className="empty-row">수락 후 활성화된 업무가 표시됩니다.</p> : myWork.map((assignment) => <article className="progress-row" key={assignment.assignment_id}><div><b>{assignment.title}</b><small>수락된 TaskAssignment</small></div><span className="status completed">{assignment.state}</span><strong>진행 중</strong></article>)}</section>
        </div>
        <section className="surface-card schedule"><div className="card-title"><h2>오늘 예정된 Workflow</h2><button onClick={() => setSurface("work")}>전체 업무 보기</button></div><div className="schedule-head"><span>순서</span><span>내용</span><span>설명</span><span /></div>{workflows.slice(0, 4).map((workflow, index) => <article className="schedule-row" key={workflow.workflow_id}><span>{String(index + 1).padStart(2, "0")}</span><b>{workflow.title}</b><small>{workflow.description}</small><button className="outline" onClick={() => { setSurface("work"); }}>업무 열기</button></article>)}</section>
      </>}
      {surface !== "today" && <section className="surface-card workflow-surface"><div className="card-title"><div><p className="kicker">{surface.toUpperCase()}</p><h2>{surface === "work" ? "내 업무와 Workflow" : surface === "report" ? "보고 Workflow" : "현재 연결된 Workflow"}</h2></div><button onClick={() => void refresh()}>새로고침</button></div><div className="workflow-list">{visibleWorkflows.map((workflow) => <article className="workflow-card" key={workflow.workflow_id}><div><span className="version">{workflow.version}</span><h3>{workflow.title}</h3><p>{workflow.description}</p>{(workflow.input_schema.required ?? []).map((field) => <label className="inline-input" key={field}>{field}<input value={inputs[`${workflow.workflow_id}:${field}`] ?? ""} placeholder={`${field} 입력`} onChange={(event) => setInputs({ ...inputs, [`${workflow.workflow_id}:${field}`]: event.target.value })} /></label>)}</div><button className="primary" disabled={busy !== null} onClick={() => void start(workflow)}>{busy === workflow.workflow_id ? "시작 중" : "시작"}</button></article>)}</div></section>}
      {run && <aside className="run-drawer"><div className="card-title"><div><p className="kicker">진행 현황</p><h2>{selected?.title}</h2></div><button onClick={() => setRun(null)}>닫기</button></div><span className={`status ${run.state}`}>{stateLabel[run.state] ?? run.state}</span><ol className="graph">{selected?.graph.nodes.map((node) => <li className={run.waiting_on.includes(node.id) ? "waiting" : ""} key={node.id}>{node.label}<small>{node.kind === "human_gate" ? "확인 단계" : node.kind === "tool" ? "자동 처리" : "업무 단계"}</small></li>)}</ol><h3>처리 결과</h3><ul className="result-list">{run.tool_results.map((item) => <li key={item.tool_name}><b>{resultLabel[item.tool_name] ?? "업무 처리가 완료되었습니다."}</b><span>처리 완료</span></li>)}</ul><h3>활동 기록</h3><ul className="audit">{run.audit.map((item, index) => <li key={`${item.event_type}-${index}`}>{auditLabel[item.event_type] ?? "업무 상태가 변경되었습니다."}</li>)}</ul></aside>}
    </section>
  </main>;
}
