import { useCallback, useEffect, useMemo, useState } from "react";

import {
  assignToProject,
  createProject,
  getMemberDirectory,
  getProject,
  getProjectParticipationHistory,
  listProjects,
  releaseFromProject,
} from "./api";
import { formatDate, personName, taskStateLabel } from "./labels";
import { Select } from "./Select";
import type { Persona, Project, ProjectDetail, ProjectParticipation } from "./viewModels";

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
  const [history, setHistory] = useState<ProjectParticipation[] | null>(null);
  const [directory, setDirectory] = useState<Persona[]>([]);
  const [opening, setOpening] = useState(false);
  const [name, setName] = useState("");
  const [joining, setJoining] = useState("");
  const [releasing, setReleasing] = useState<{
    assignmentId?: string;
    memberId: string;
    displayName: string;
  } | null>(null);
  const [releaseReason, setReleaseReason] = useState("");
  const [busy, setBusy] = useState(false);

  const loadProject = useCallback(async (projectId: string) => {
    setReleasing(null);
    setReleaseReason("");
    const [detail, participationHistory] = await Promise.all([
      getProject(projectId),
      getProjectParticipationHistory(projectId),
    ]);
    setSelected(detail);
    setHistory(participationHistory);
  }, []);

  const reload = useCallback(
    async (keep?: string) => {
      try {
        const rows = await listProjects();
        setProjects(rows);
        const target = keep ?? selected?.project_id ?? rows[0]?.project_id;
        if (target) {
          await loadProject(target);
        } else {
          setSelected(null);
          setHistory(null);
        }
        onError(null);
      } catch (error) {
        onError(error instanceof Error ? error.message : "프로젝트를 불러오지 못했습니다.");
      }
    },
    // 선택은 사용자 조작으로만 바뀐다: 목록을 다시 읽을 때마다 선택이 튀지 않게 한다.
    [loadProject, onError, selected?.project_id],
  );

  useEffect(() => {
    void listProjects()
      .then(async (rows) => {
        setProjects(rows);
        if (rows[0]) await loadProject(rows[0].project_id);
      })
      .catch((error: unknown) => onError(error instanceof Error ? error.message : "프로젝트를 불러오지 못했습니다."));
    void getMemberDirectory().then(setDirectory).catch(() => setDirectory([]));
  }, [loadProject, onError, personaId]);

  const joined = new Set((selected?.members ?? []).map((row) => row.member_id));
  const iAmIn = joined.has(personaId);

  async function open() {
    if (!name.trim() || busy) return;
    setBusy(true);
    try {
      const made = await createProject({ name: name.trim() });
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
      await releaseFromProject(selected.project_id, memberId, releasing?.assignmentId, releaseReason);
      setReleasing(null);
      setReleaseReason("");
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
          부서를 가로질러 묶이는 일입니다. 소유 조직은 없고, 붙은 사람이 곧 그 프로젝트입니다 — 어느 부서에서든 붙을 수 있습니다.
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
              <button className="btn h30 primary" disabled={busy || !name.trim()} onClick={() => void open()} type="button">
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
                  onClick={() => void loadProject(project.project_id)}
                  type="button"
                >
                  <span className="project-row-name">{project.name}</span>
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
            {(selected.starts_on || selected.ends_on) && (
              <p className="t-meta">
                {selected.starts_on && formatDate(selected.starts_on)}
                {selected.ends_on && ` ~ ${formatDate(selected.ends_on)}`}
              </p>
            )}
            {selected.description && <p>{selected.description}</p>}

            <section aria-label="현재 참여자">
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
                      <button
                        aria-label={`${personName(row.display_name)} 참여 종료`}
                        className="btn link"
                        disabled={busy}
                        onClick={() => {
                          setReleasing({
                            assignmentId: row.assignment_id,
                            memberId: row.member_id,
                            displayName: personName(row.display_name),
                          });
                          setReleaseReason("");
                        }}
                        type="button"
                      >
                        참여 종료
                      </button>
                    )}
                  </li>
                ))}
                {selected.members.length === 0 && <li className="t-meta">아직 아무도 붙어 있지 않습니다.</li>}
              </ul>
              {releasing && (
                <div className="project-join">
                  <label className="field" htmlFor="project-release-reason">
                    <span>참여 종료 사유 (선택)</span>
                    <input
                      id="project-release-reason"
                      maxLength={4000}
                      onChange={(event) => setReleaseReason(event.target.value)}
                      placeholder={`${releasing.displayName}의 종료 사유`}
                      value={releaseReason}
                    />
                  </label>
                  <button className="btn h30" disabled={busy} onClick={() => void release(releasing.memberId)} type="button">
                    종료 기록
                  </button>
                  <button
                    className="btn h30"
                    disabled={busy}
                    onClick={() => {
                      setReleasing(null);
                      setReleaseReason("");
                    }}
                    type="button"
                  >
                    취소
                  </button>
                </div>
              )}
              {selected.may_manage && (
              <div className="project-join">
                <Select
                  label="붙일 구성원"
                  onChange={setJoining}
                  options={directory
                    .filter((person) => !joined.has(person.id))
                    .map((person) => ({ value: person.id, label: personName(person.display_name) }))}
                  placeholder="구성원 선택"
                  value={joining}
                />
                <button className="btn h30" disabled={busy || !joining} onClick={() => void join(joining, "member")} type="button">
                  참여로 붙이기
                </button>
                <button className="btn h30" disabled={busy || !joining} onClick={() => void join(joining, "lead")} type="button">
                  담당으로 붙이기
                </button>
              </div>
              )}
            </section>

            <section aria-label="참여 이력">
              <b>참여 이력 {history === null ? "" : `${history.length}건`}</b>
              <ul className="project-members">
                {(history ?? []).map((row) => (
                  <li data-assignment-id={row.assignment_id} key={row.assignment_id}>
                    <span className="project-member-kind">{row.assignment_kind === "lead" ? "담당" : "참여"}</span>
                    <span>{personName(row.display_name)}</span>
                    <span className="t-meta">{row.ended_at ? "종료" : "현재"}</span>
                    {row.ended_by_display_name && <span className="t-meta">처리 {personName(row.ended_by_display_name)}</span>}
                    {row.end_reason && <span>{row.end_reason}</span>}
                  </li>
                ))}
                {history !== null && history.length === 0 && <li className="t-meta">기록된 참여가 없습니다.</li>}
              </ul>
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
