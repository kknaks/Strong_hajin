import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  assignToProject,
  createProject,
  getMemberDirectory,
  getProject,
  getProjectParticipationHistory,
  getTask,
  listProjects,
  releaseFromProject,
  transitionDirectTask,
} from "../../lib/api";
import { Button } from "../../ds/Button";
import { Empty } from "../../ds/Empty";
import { projectScreen, seoulToday } from "../../lib/labels";
import type { DirectTask, Persona, Project, ProjectDetail, ProjectParticipation } from "../../lib/viewModels";
/* 전이의 어휘는 **업무 화면이 갖는다** — 이 화면은 그 타입과 부품을 «가져다 쓴다» (D-32). */
import type { TaskAction } from "../work/WorkModals";
import { ProjectCreateModal, type ProjectCreateInput } from "./ProjectCreateModal";
import { ProjectGantt } from "./ProjectGantt";
import { ProjectManageModal } from "./ProjectManageModal";
import { ProjectRail } from "./ProjectRail";
import { ProjectSummaryStrip } from "./ProjectSummaryStrip";
import { ProjectTaskPanel } from "./ProjectTaskPanel";
import { railCards, summarize } from "./projectModel";

/**
 * 프로젝트 — 부서를 가로질러 묶이는 일과, 그 일이 **어느 기간에 걸쳐 있고 무엇이 무엇보다 먼저인지**.
 *
 * 화면은 **3레일**이다 (WORK-005): 좌 레일이 프로젝트 셀렉터 + 업무 카드, 본문이 요약 스트립 + 진행
 * 라인(간트 + 의존선), 우 레일이 선택된 업무 하나의 상자다. 레일은 셸의 `AppBody` 슬롯에 등록한다 —
 * 선례 `features/work/MyWorkPage.tsx:941-972` · `features/calendar/CalendarPage.tsx:460-477`.
 *
 * **상호작용 축은 둘뿐이다**: 프로젝트(셀렉터) 하나가 데이터의 범위를 정하고, `taskId` 하나가
 * **좌 레일 카드 · 간트 행/바 · 의존선 · 우 레일** 넷을 동시에 묶는다. 어디서 골라도 나머지 셋이
 * 같이 반응한다. 그 밖의 상태는 간트의 접힘과 관리 모달뿐이다.
 *
 * **관리 기능은 모달로 옮겼다** (D-05) — 참여자 붙이기/떼기 · 참여 이력 · 새 프로젝트. 본문은
 * 「이 프로젝트의 일이 어떻게 흐르는가」만 말하고, 사람을 붙이고 떼는 것은 그것을 하러 들어가는 자리다.
 * 손잡이는 셸 머리의 `actions` 이고 **`may_manage` 가 거짓이면 아예 렌더되지 않는다.**
 *
 * 기간과 담당은 비어 있을 수 있다. 비어 있는 것을 `미정` 이라 쓰지 않고 그냥 비워 둔다.
 */
export function ProjectPage({
  personaId,
  onError,
  onNotice,
  onRegisterRails,
  onRegisterHeaderActions,
  onOpenTask,
  canManageOwnTasks = false,
}: {
  personaId: string;
  onError: (message: string | null) => void;
  /**
   * 성공을 말하는 자리 — 셸의 토스트다. **완료 보고가 올라갔다**는 확인 문구가 이 길로 간다
   * (선례 `features/work/MyWorkPage.tsx:1421-1422` 가 `onError` 와 «둘을 함께» 넘긴다).
   */
  onNotice?: (message: string) => void;
  onRegisterRails?: (rails: { left?: React.ReactNode; right?: React.ReactNode }) => void;
  onRegisterHeaderActions?: (node: React.ReactNode) => void;
  /** 업무를 **여는** 길 — 편집은 업무 화면의 드로어에서만 된다 (D-20). */
  onOpenTask?: (taskId: string) => void;
  /**
   * **「자기 업무 관리」 역량**(세션 봉투의 `task.self_manage`) — 상태 드롭다운의 **게이트 둘 중 하나**다 (D-32).
   *
   * ⚠ 나머지 하나는 **업무 상세의 접근 값**이다. 하나만 보면 **역량 없는 담당자에게 드롭다운이 서고
   * 고를 때마다 서버가 거절한다.**
   */
  canManageOwnTasks?: boolean;
}) {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [selected, setSelected] = useState<ProjectDetail | null>(null);
  const [history, setHistory] = useState<ProjectParticipation[] | null>(null);
  const [directory, setDirectory] = useState<Persona[]>([]);
  const [loadState, setLoadState] = useState<"loading" | "error" | "ready">("loading");
  /** 선택 축 — 이 하나가 좌 레일 · 간트 · 의존선 · 우 레일 넷을 움직인다. */
  const [taskId, setTaskId] = useState<string | null>(null);
  /** 접힌 가지. **기본은 모두 펼침**이라 비어 있는 것이 기본값이다 (D-16). */
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(() => new Set());
  const [taskDetail, setTaskDetail] = useState<DirectTask | null>(null);
  const [taskDetailState, setTaskDetailState] = useState<"idle" | "loading" | "error" | "ready">("idle");
  const [managing, setManaging] = useState(false);
  /** 생성 «전용» 모달 — 관리 모달과 다른 겹이다 (D-30). */
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  /**
   * 업무 상세를 **다시 읽게 하는 회차**. 상태를 바꾸고 나면 이 값이 올라가고 아래 effect 가 다시 돈다 —
   * 낙관적 갱신을 하지 않으므로 **새 상태는 서버에서 온 것뿐**이다 (L-52).
   */
  const [detailNonce, setDetailNonce] = useState(0);
  const today = useMemo(() => seoulToday(), []);

  const loadProject = useCallback(async (projectId: string) => {
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
        const target = keep ?? rows[0]?.project_id;
        if (target) {
          await loadProject(target);
        } else {
          setSelected(null);
          setHistory(null);
        }
        setLoadState("ready");
        onError(null);
      } catch (error) {
        setLoadState("error");
        onError(error instanceof Error ? error.message : projectScreen.loadFailed);
      }
    },
    [loadProject, onError],
  );

  useEffect(() => {
    void reload();
    void getMemberDirectory().then(setDirectory).catch(() => setDirectory([]));
    // 사람이 바뀌면 보이는 프로젝트도 바뀐다 — 그때만 처음부터 다시 읽는다.
  }, [personaId, reload]);

  const tasks = useMemo(() => selected?.tasks ?? [], [selected]);
  const selectedTask = useMemo(() => tasks.find((task) => task.task_id === taskId) ?? null, [tasks, taskId]);

  /* 고른 업무가 새 응답에 없으면 선택을 놓는다 — 없는 업무의 상자를 열어 두지 않는다. */
  useEffect(() => {
    if (taskId !== null && selectedTask === null) setTaskId(null);
  }, [selectedTask, taskId]);

  /**
   * **선택된 하나에만** 상세를 부른다 (D-20 · FE-3 작업 4).
   *
   * 체크리스트 «항목» 과 요청자는 목록 응답에 없다 — 그렇다고 목록 전부를 상세로 긁으면
   * **업무 수만큼 호출**이 된다. 그래서 선택이 바뀔 때 한 번만 묻는다.
   */
  useEffect(() => {
    if (taskId === null) {
      setTaskDetail(null);
      setTaskDetailState("idle");
      return;
    }
    let live = true;
    setTaskDetailState("loading");
    void getTask(taskId)
      .then((detail) => {
        if (!live) return;
        setTaskDetail(detail);
        setTaskDetailState("ready");
      })
      .catch(() => {
        if (!live) return;
        setTaskDetail(null);
        setTaskDetailState("error");
      });
    return () => {
      live = false;
    };
  }, [detailNonce, taskId]);

  const chooseProject = useCallback(
    (projectId: string) => {
      if (projectId === selected?.project_id) return;
      /* 프로젝트가 바뀌면 그 안의 선택도 함께 놓는다 — 남은 선택은 다른 프로젝트의 것이다. */
      setTaskId(null);
      setCollapsed(new Set());
      setManaging(false);
      void loadProject(projectId).catch((error: unknown) => {
        onError(error instanceof Error ? error.message : projectScreen.loadFailed);
        /* 못 읽었다 — **목록을 다시 읽는다.** 배정을 거절하면 그 프로젝트가 «목록에서 사라지는데»
           캐시한 목록만 들고 있으면 「없는 프로젝트를 고른 상태」가 셀렉터에 남는다. */
        void reload();
      });
    },
    [loadProject, onError, reload, selected?.project_id],
  );

  const toggleBranch = useCallback((branchId: string) => {
    setCollapsed((current) => {
      const next = new Set(current);
      if (next.has(branchId)) next.delete(branchId);
      else next.add(branchId);
      return next;
    });
  }, []);

  /* 여는 콜백은 레일 등록 effect 가 물고 있다 — 부르는 쪽이 인라인 화살표로 넘기면 매 렌더
     새 함수가 되어 «등록 → 셸 상태 변경 → 렌더 → 다시 등록» 으로 무한히 돈다. 그래서 최신 것을
     ref 로 들고 이 함수 자체는 절대 바뀌지 않게 둔다 (선례 `ds/Modal.tsx` 의 `useEscape`). */
  const latestOpenTask = useRef(onOpenTask);
  useEffect(() => {
    latestOpenTask.current = onOpenTask;
  });
  const openTask = useCallback((openId: string) => {
    setTaskId(openId);
    latestOpenTask.current?.(openId);
  }, []);

  /* 전이도 같은 함정에 걸린다 — 레일 등록 effect 가 이 함수를 물고 있으므로 «렌더마다 새 함수» 이면
     등록이 자기 자신을 다시 깨운다. 최신 것을 ref 로 들고 넘기는 얼굴은 절대 안 바뀌게 둔다. */
  const latestTransition = useRef(transition);
  useEffect(() => {
    latestTransition.current = transition;
  });
  const runTransition = useCallback(
    (task: DirectTask, action: TaskAction, reason?: string) => latestTransition.current(task, action, reason),
    [],
  );
  /* 완료 보고는 전이를 안 지나고도 상태를 옮긴다 — 그 뒤 다시 읽는 자리도 같은 방식으로 고정한다. */
  const latestRefresh = useRef(refreshAfterWrite);
  useEffect(() => {
    latestRefresh.current = refreshAfterWrite;
  });
  const runRefresh = useCallback(() => latestRefresh.current(), []);

  /**
   * 헤더의 「프로젝트 추가」가 만든다 — **네 칸** (D-30 · §2.10).
   *
   * **거절 문구를 «돌려준다»** — 모달이 그것을 자기 안에서 말하고 닫히지 않는다. 화면 아래 토스트로
   * 보내면 「무엇을 고쳐야 하나」가 쓰던 칸에서 멀어진다. 성공하면 `null` 이고, 그때만 닫힌다.
   *
   * **만든 사람은 리드로 붙는다**(서버의 기존 동작) — 그래서 `reload(made.project_id)` 한 번으로
   * 셀렉터 목록과 그 프로젝트가 함께 온다 (L-22).
   */
  async function createFromHeader(input: ProjectCreateInput): Promise<string | null> {
    if (busy) return null;
    setBusy(true);
    try {
      const made = await createProject(input);
      await reload(made.project_id);
      setCreating(false);
      return null;
    } catch (error) {
      return error instanceof Error ? error.message : projectScreen.createFailed;
    } finally {
      setBusy(false);
    }
  }

  /**
   * 상태가 바뀐 뒤 **다시 읽는다** — 낙관적 갱신 0건 (D-32 ③-차 · L-52).
   *
   * **다시 읽는 범위가 업무 상세 하나가 아니다.** 같은 상태를 **좌 레일 카드와 간트 바**도 그리므로
   * **프로젝트 상세까지** 읽는다 — 1루프의 「선택 하나가 넷을 움직인다」와 같은 축이다.
   */
  async function refreshAfterWrite() {
    if (selected) {
      /* 고른 프로젝트가 사라졌을 수도 있다(배정 거절의 자동 해제) — 그때는 **목록부터 다시 읽는다.**
         목록을 캐시해 두면 «없는 프로젝트를 고른 상태» 가 셀렉터에 남는다. */
      await loadProject(selected.project_id).catch(() => reload());
    }
    setDetailNonce((nonce) => nonce + 1);
  }

  /**
   * 상태 전이 — **업무 화면의 경로를 그대로 부른다** (D-32). 새 명령도 새 라우트도 없다.
   *
   * **회차는 「업무 상세로 읽은 그 업무」의 것**이다 — `tasks[]` 행에는 `version` 이 아예 없고,
   * 한 걸음 뒤진 회차를 보내면 **409** 다.
   *
   * **낙관적 갱신을 하지 않는다.** 성공하면 **프로젝트 상세와 업무 상세를 둘 다 다시 읽는다** —
   * 같은 상태를 좌 레일 카드·간트 바도 그리기 때문이다. 실패하면 **아무것도 안 바뀌고 문구만** 뜬다.
   */
  async function transition(task: DirectTask, action: TaskAction, reason?: string): Promise<boolean> {
    if (busy) return false;
    setBusy(true);
    try {
      await transitionDirectTask(task.task_id, action, task.version, reason);
      await refreshAfterWrite();
      onError(null);
      return true;
    } catch (error) {
      onError(error instanceof Error ? error.message : projectScreen.transitionFailed);
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function create(name: string) {
    if (!name || busy) return;
    setBusy(true);
    try {
      const made = await createProject({ name });
      await reload(made.project_id);
    } catch (error) {
      onError(error instanceof Error ? error.message : projectScreen.newProjectFailed);
    } finally {
      setBusy(false);
    }
  }

  async function join(memberId: string, kind: "lead" | "member") {
    if (!selected || !memberId || busy) return;
    setBusy(true);
    try {
      await assignToProject(selected.project_id, { member_id: memberId, kind });
      await loadProject(selected.project_id);
    } catch (error) {
      onError(error instanceof Error ? error.message : projectScreen.joinFailed);
    } finally {
      setBusy(false);
    }
  }

  async function release(memberId: string, assignmentId: string | undefined, reason: string) {
    if (!selected || busy) return;
    setBusy(true);
    try {
      await releaseFromProject(selected.project_id, memberId, assignmentId, reason);
      await loadProject(selected.project_id);
    } catch (error) {
      onError(error instanceof Error ? error.message : projectScreen.releaseFailed);
    } finally {
      setBusy(false);
    }
  }

  const noProjects = projects !== null && projects.length === 0;
  /* **목록을 아직 «모르는» 동안**(첫 적재). 0개인 사람에게 레일이 먼저 섰다가 빈 상태가 덮으면
     「네 칸이 각각 비는」 프레임이 실재하게 된다 (D-22) — 모르는 동안은 아무것도 세우지 않는다.
     실패는 모르는 것이 아니라 «알아내지 못한 것»이다: 그때는 레일이 서서 다시 시도할 손잡이를 낸다. */
  const listUnknown = projects === null && loadState !== "error";
  const cards = useMemo(() => railCards(tasks), [tasks]);
  const summary = useMemo(() => summarize(tasks), [tasks]);
  const iAmIn = (selected?.members ?? []).some((row) => row.member_id === personaId);

  /* 레일 두 칸을 셸의 AppBody 슬롯으로 넘긴다. 읽을 수 있는 프로젝트가 0개면 — 또는 아직 모르면 —
     레일을 세우지 않는다: 화면 전체를 한 문장이 덮는 자리이고, 네 칸을 각각 비우지 않는다 (D-22). */
  useEffect(() => {
    if (!onRegisterRails) return;
    if (noProjects || listUnknown) {
      onRegisterRails({});
      return;
    }
    onRegisterRails({
      left: (
        <ProjectRail
          cards={cards}
          onProject={chooseProject}
          onRetry={() => void reload(selected?.project_id)}
          onTask={setTaskId}
          projectId={selected?.project_id ?? ""}
          projects={projects ?? []}
          state={loadState}
          taskId={taskId}
        />
      ),
      right: (
        <ProjectTaskPanel
          busy={busy}
          canManageOwnTasks={canManageOwnTasks}
          detail={taskDetail}
          detailState={taskDetailState}
          onOpenTask={openTask}
          onTask={setTaskId}
          onChanged={runRefresh}
          /* 완료 보고 모달의 **유일한 오류 통로**다 — 빈 함수로 받으면 서버의 거절이 갈 곳이 없다
             (선례 `MyWorkPage.tsx:1421-1422`). 거절 갈래가 넷이라 자주 지나는 자리다. */
          onError={onError}
          onNotice={onNotice}
          onTransition={runTransition}
          row={selectedTask}
          tasks={tasks}
        />
      ),
    });
    return () => onRegisterRails({});
  }, [
    busy,
    canManageOwnTasks,
    cards,
    chooseProject,
    listUnknown,
    loadState,
    noProjects,
    onError,
    onNotice,
    onRegisterRails,
    openTask,
    projects,
    reload,
    runRefresh,
    runTransition,
    selected?.project_id,
    selectedTask,
    taskDetail,
    taskDetailState,
    taskId,
    tasks,
  ]);

  /**
   * 머리의 손잡이 **둘** — 등록하고 **떠날 때 지운다** (D-21 · D-30 · L-14 · L-18).
   *
   * ⚠ **게이트는 «하나뿐»이다 — 「관리」 쪽이다** (사용자 확정, 2026-09-22).
   * - 「프로젝트 관리」는 **고른 그 프로젝트의 `may_manage`** — 서버가 낸 값이고 화면이 추측하지 않는다.
   *   **자동 초대로 붙은 사람은 `참여`(member)라 이 값이 «여전히 거짓»** 이다 — 붙었다고 손잡이가 서지 않는다.
   * - 「프로젝트 추가」는 **누구에게나 선다.** ~~`project.manage` 역량 게이트~~ 는 없앴다:
   *   **만드는 것은 모든 사람이 한다.** 자격이 없는 사람이 눌러 보내면 **서버가 거절하고 그 문구가
   *   모달 «안»에 선다**(`createFromHeader` 가 문구를 돌려준다) — 버튼을 감춰 「왜 없지」를 만들지 않는다.
   */
  useEffect(() => {
    if (!onRegisterHeaderActions) return;
    const manage = selected?.may_manage ? (
      <Button key="manage" onClick={() => setManaging(true)} size="sm" type="button" variant="outlined">
        {projectScreen.manage}
      </Button>
    ) : null;
    /* 생성이 «오른쪽 끝» 이다 — 같은 슬롯이고 새 슬롯을 만들지 않는다 (D-25). */
    const add = (
      <Button key="add" onClick={() => setCreating(true)} size="sm" tone="primary" type="button" variant="solid">
        {projectScreen.createProject}
      </Button>
    );
    onRegisterHeaderActions([manage, add]);
    return () => onRegisterHeaderActions(null);
  }, [onRegisterHeaderActions, selected?.may_manage]);

  /* 생성 모달은 **빈 상태 갈래에서도 서야 한다** — 그래서 두 갈래가 같은 노드를 쓴다 (L-15). */
  const createModal = creating ? (
    <ProjectCreateModal busy={busy} onClose={() => setCreating(false)} onCreate={createFromHeader} />
  ) : null;

  if (noProjects) {
    return (
      <section aria-label={projectScreen.noProjectsTitle} className="scax-pj-view scax-pj-view--empty">
        {/* 본문은 **한 문장 그대로** 덮는다 (D-22). 머리의 생성 손잡이는 **그 덮개 밖**이다 —
            「빈 상태」와 「만들 수 없다」는 다른 말이고, 첫 프로젝트를 만들 길이 여기서 끊기면 안 된다. */}
        <Empty description={projectScreen.noProjectsDescription} icon="folder" title={projectScreen.noProjectsTitle} />
        {createModal}
      </section>
    );
  }

  return (
    <section aria-label={selected?.name ?? projectScreen.railTitle} className="scax-pj-view">
      <ProjectSummaryStrip summary={summary} />
      {/* 업무가 0건인 이유가 「없어서」인지 「안 보여서」인지는 다른 말이다 — 붙어 있지 않으면 그 사실을 말한다. */}
      {tasks.length === 0 && selected !== null && !iAmIn && <p className="scax-pj-view__note">{projectScreen.notMember}</p>}
      <ProjectGantt
        collapsed={collapsed}
        onTask={setTaskId}
        onToggle={toggleBranch}
        /* 프로젝트가 바뀌면 «그» 프로젝트의 오늘 자리로 다시 연다 — 그 판정을 간트가 이 값으로 한다. */
        projectId={selected?.project_id ?? ""}
        tasks={tasks}
        taskId={taskId}
        today={today}
      />
      {managing && selected && (
        <ProjectManageModal
          busy={busy}
          directory={directory}
          history={history}
          onClose={() => setManaging(false)}
          onCreate={(name) => void create(name)}
          onJoin={(memberId, kind) => void join(memberId, kind)}
          onRelease={(memberId, assignmentId, reason) => void release(memberId, assignmentId, reason)}
          project={selected}
        />
      )}
      {createModal}
    </section>
  );
}
