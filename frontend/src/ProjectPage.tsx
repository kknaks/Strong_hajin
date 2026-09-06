import { useCallback, useEffect, useMemo, useState } from "react";

import { assignToProject, createProject, getMemberDirectory, getProject, getOrganizationTree, listProjects, releaseFromProject } from "./api";
import { formatDate, personName, taskStateLabel } from "./labels";
import type { OrganizationUnitNode, Persona, Project, ProjectDetail } from "./viewModels";

/**
 * 프로젝트 — 부서를 가로질러 묶이는 일과, 그 일을 함께 하는 사람들.
 *
 * 여기 보이는 프로젝트는 내가 붙어 있거나 내 조직이 소유한 것들이다. 그 안의 업무는 다르다: 붙어 있는
 * 프로젝트의 업무만 보인다. 만든 사람도 예외가 아니어서, 자기가 연 프로젝트의 일을 보려면 자기를 담당자로
 * 붙여야 한다 — 볼 수 있는 이유가 언제나 원장에 적혀 있게 하기 위해서다.
 *
 * 기간과 담당 기간은 비어 있을 수 있다. 비어 있는 것을 `미정`이라 쓰지 않고 그냥 비워 둔다.
 */
export function ProjectPage({ personaId, onError }: { personaId: string; onError: (message: string | null) => void }) {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [selected, setSelected] = useState<ProjectDetail | null>(null);
  const [units, setUnits] = useState<OrganizationUnitNode[]>([]);
  const [directory, setDirectory] = useState<Persona[]>([]);
  const [opening, setOpening] = useState(false);
  const [name, setName] = useState("");
  const [unit, setUnit] = useState("");
  const [joining, setJoining] = useState("");
  const [busy, setBusy] = useState(false);

  const reload = useCallback(
    async (keep?: string) => {
      try {
        const rows = await listProjects();
        setProjects(rows);
        const target = keep ?? selected?.project_id ?? rows[0]?.project_id;
        setSelected(target ? await getProject(target) : null);
        onError(null);
      } catch (error) {
        onError(error instanceof Error ? error.message : "프로젝트를 불러오지 못했습니다.");
      }
    },
    // 선택은 사용자 조작으로만 바뀐다: 목록을 다시 읽을 때마다 선택이 튀지 않게 한다.
    [onError, selected?.project_id],
  );

  useEffect(() => {
    void listProjects()
      .then(async (rows) => {
        setProjects(rows);
        if (rows[0]) setSelected(await getProject(rows[0].project_id));
      })
      .catch((error: unknown) => onError(error instanceof Error ? error.message : "프로젝트를 불러오지 못했습니다."));
    void getOrganizationTree().then(setUnits).catch(() => setUnits([]));
    void getMemberDirectory().then(setDirectory).catch(() => setDirectory([]));
  }, [onError, personaId]);

  const unitName = useMemo(() => new Map(units.map((item) => [item.id, item.name])), [units]);
  const joined = new Set((selected?.members ?? []).map((row) => row.member_id));
  const iAmIn = joined.has(personaId);

  async function open() {
    if (!name.trim() || !unit || busy) return;
    setBusy(true);
    try {
      const made = await createProject({ name: name.trim(), organization_unit_id: unit });
      setName("");
      setOpening(false);
      await reload(made.project_id);
    } catch (error) {
      onError(error instanceof Error ? error.message : "프로젝트를 열지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  async function join(memberId: string, kind: "lead" | "member") {
    if (!selected || busy) return;
    setBusy(true);
    try {
      await assignToProject(selected.project_id, { member_id: memberId, kind });
      setJoining("");
      await reload(selected.project_id);
    } catch (error) {
      onError(error instanceof Error ? error.message : "담당자를 붙이지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  async function release(memberId: string) {
    if (!selected || busy) return;
    setBusy(true);
    try {
      await releaseFromProject(selected.project_id, memberId);
      await reload(selected.project_id);
    } catch (error) {
      onError(error instanceof Error ? error.message : "담당자를 떼지 못했습니다.");
    } finally {
      setBusy(false);
    }
  }

  const done = (selected?.tasks ?? []).filter((task) => task.state === "done").length;
  const total = selected?.tasks.length ?? 0;

  return (
    <section aria-label="프로젝트" className="project-page">
      <header className="page-head">
        <h2>프로젝트</h2>
        <p className="t-meta">
          부서를 가로질러 묶이는 일입니다. 소유 조직은 책임 소재일 뿐이고, 담당자는 어느 부서에서든 붙을 수 있습니다.
        </p>
      </header>

      <div className="project-layout">
        <aside className="project-list">
          <div className="project-list-head">
            <b>{projects === null ? "불러오는 중" : `${projects.length}개`}</b>
            <button className="btn h30" onClick={() => setOpening((current) => !current)} type="button">
              {opening ? "취소" : "새 프로젝트"}
            </button>
          </div>
          {opening && (
            <div className="project-new">
              <label className="field" htmlFor="project-name">
                <span>이름</span>
                <input id="project-name" onChange={(event) => setName(event.target.value)} value={name} />
              </label>
              <label className="field" htmlFor="project-unit">
                <span>소유 조직</span>
                <select id="project-unit" onChange={(event) => setUnit(event.target.value)} value={unit}>
                  <option value="">조직 선택</option>
                  {units.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </label>
              <button className="btn h30 primary" disabled={busy || !name.trim() || !unit} onClick={() => void open()} type="button">
                열기
              </button>
            </div>
          )}
          {projects !== null && projects.length === 0 && <p className="t-meta">아직 볼 수 있는 프로젝트가 없습니다.</p>}
          <ul>
            {(projects ?? []).map((project) => (
              <li key={project.project_id}>
                <button
                  aria-current={selected?.project_id === project.project_id}
                  className={`project-row ${selected?.project_id === project.project_id ? "on" : ""}`}
                  onClick={() => void getProject(project.project_id).then(setSelected)}
                  type="button"
                >
                  <span className="project-row-name">{project.name}</span>
                  <span className="t-meta">{project.organization_unit_name ?? unitName.get(project.organization_unit_id)}</span>
                </button>
              </li>
            ))}
          </ul>
        </aside>

        {selected === null ? (
          <div className="project-detail empty">
            <p className="t-meta">프로젝트를 고르면 담당자와 그 프로젝트의 업무를 봅니다.</p>
          </div>
        ) : (
          <div className="project-detail" data-project-id={selected.project_id}>
            <h3>{selected.name}</h3>
            <p className="t-meta">
              {selected.organization_unit_name ?? unitName.get(selected.organization_unit_id)}
              {selected.starts_on && ` · ${formatDate(selected.starts_on)}`}
              {selected.ends_on && ` ~ ${formatDate(selected.ends_on)}`}
            </p>
            {selected.description && <p>{selected.description}</p>}

            <section aria-label="담당자">
              <b>
                담당자 {selected.members.length}명 <small className="t-meta">· 부서와 무관하게 붙습니다</small>
              </b>
              <ul className="project-members">
                {selected.members.map((row) => (
                  <li data-member-id={row.member_id} key={row.member_id}>
                    <span className="project-member-kind">{row.assignment_kind === "lead" ? "담당" : "참여"}</span>
                    <span>{personName(row.display_name)}</span>
                    {row.valid_until && <span className="t-meta">~ {formatDate(row.valid_until.slice(0, 10))}</span>}
                    {selected.may_manage && (
                      <button className="btn link" disabled={busy} onClick={() => void release(row.member_id)} type="button">
                        떼기
                      </button>
                    )}
                  </li>
                ))}
                {selected.members.length === 0 && <li className="t-meta">아직 아무도 붙어 있지 않습니다.</li>}
              </ul>
              {selected.may_manage && (
              <div className="project-join">
                <select aria-label="붙일 구성원" onChange={(event) => setJoining(event.target.value)} value={joining}>
                  <option value="">구성원 선택</option>
                  {directory
                    .filter((person) => !joined.has(person.id))
                    .map((person) => (
                      <option key={person.id} value={person.id}>
                        {personName(person.display_name)}
                      </option>
                    ))}
                </select>
                <button className="btn h30" disabled={busy || !joining} onClick={() => void join(joining, "member")} type="button">
                  참여로 붙이기
                </button>
                <button className="btn h30" disabled={busy || !joining} onClick={() => void join(joining, "lead")} type="button">
                  담당으로 붙이기
                </button>
              </div>
              )}
            </section>

            <section aria-label="이 프로젝트의 업무">
              <b>
                업무 {total}건{total > 0 && <small className="t-meta"> · 완료 {done}건</small>}
              </b>
              {!iAmIn && (
                <p className="t-meta">
                  이 프로젝트의 업무는 담당자에게만 보입니다. 나를 담당자로 붙이면 여기에서 함께 봅니다.
                </p>
              )}
              <ul className="project-tasks">
                {selected.tasks.map((task) => (
                  <li className={task.parent_task_id ? "child" : ""} data-task-id={task.task_id} key={task.task_id}>
                    <span>{task.title}</span>
                    <span className="t-meta">{taskStateLabel[task.state as keyof typeof taskStateLabel] ?? task.state}</span>
                    {task.due_date && <span className="t-meta">기한 {formatDate(task.due_date)}</span>}
                  </li>
                ))}
                {total === 0 && <li className="t-meta">{iAmIn ? "아직 이 프로젝트의 업무가 없습니다." : ""}</li>}
              </ul>
            </section>
          </div>
        )}
      </div>
    </section>
  );
}
