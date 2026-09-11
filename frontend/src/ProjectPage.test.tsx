import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("./api", () => ({
  assignToProject: vi.fn(),
  createProject: vi.fn(),
  getMemberDirectory: vi.fn(),
  getOrganizationTree: vi.fn(),
  getProject: vi.fn(),
  getProjectParticipationHistory: vi.fn(),
  listProjects: vi.fn(),
  releaseFromProject: vi.fn(),
}));

import * as api from "./api";
import { ProjectPage } from "./ProjectPage";

const project = {
  project_id: "p-1",
  name: "한빛의원 통합 마케팅",
  description: "홈페이지·네이버·플레이스",
  organization_unit_id: "domestic",
  organization_unit_name: "국내사업부",
  state: "active",
  starts_on: "2026-09-01",
  ends_on: null,
  external_key: null,
  version: 1,
};

const detail = {
  ...project,
  may_manage: true,
  members: [
    { assignment_id: "current-mina", member_id: "mina", display_name: "민아", assignment_kind: "lead", valid_from: null, valid_until: null },
    { assignment_id: "current-jiho", member_id: "jiho", display_name: "지호", assignment_kind: "member", valid_from: null, valid_until: null },
  ],
  tasks: [
    { task_id: "t-1", title: "한빛 9월 통합 마케팅", state: "open", start_date: null, due_date: "2026-10-03", parent_task_id: null },
    { task_id: "t-2", title: "홈페이지 디자인 기획", state: "done", start_date: null, due_date: "2026-09-12", parent_task_id: "t-1" },
  ],
};

const history = [
  {
    assignment_id: "ended-1",
    member_id: "jiho",
    display_name: "지호",
    assignment_kind: "member" as const,
    valid_from: null,
    valid_until: null,
    assigned_by_member_id: "mina",
    assigned_by_display_name: "민아",
    created_at: "2026-09-01T00:00:00+00:00",
    ended_at: "2026-09-10T00:00:00+00:00",
    ended_by_member_id: "mina",
    ended_by_display_name: "민아",
    end_reason: "1차 지원 종료",
  },
];

function renderPage(personaId = "mina", overrides: Record<string, unknown> = {}) {
  vi.mocked(api.listProjects).mockResolvedValue([project] as never);
  vi.mocked(api.getProject).mockResolvedValue({ ...detail, ...overrides } as never);
  vi.mocked(api.getProjectParticipationHistory).mockResolvedValue(history);
  vi.mocked(api.getOrganizationTree).mockResolvedValue([{ id: "domestic", name: "국내사업부", parent_id: "thesc" }] as never);
  vi.mocked(api.getMemberDirectory).mockResolvedValue([
    { id: "mina", display_name: "민아" },
    { id: "jiho", display_name: "지호" },
    { id: "m-han", display_name: "한별" },
  ] as never);
  return render(<ProjectPage onError={vi.fn()} personaId={personaId} />);
}

describe("프로젝트", () => {
  afterEach(cleanup);

  it("담당자가 어느 부서 사람인지 묻지 않고, 그 프로젝트의 업무를 계층 그대로 보여 준다", async () => {
    const { container } = renderPage();
    await screen.findByText("한빛의원 통합 마케팅", { selector: "h3" });

    const people = container.querySelector('[aria-label="현재 참여자"]') as HTMLElement;
    expect(within(people).getByText("민아")).toBeTruthy();
    expect(within(people).getByText("지호")).toBeTruthy();
    // 담당과 참여는 다른 말이다.
    expect(people.textContent).toContain("담당");
    expect(people.textContent).toContain("참여");

    const work = container.querySelector('[aria-label="이 프로젝트의 업무"]') as HTMLElement;
    expect(work.textContent).toContain("업무 2건");
    expect(work.textContent).toContain("완료 1건");
    // 하위 업무는 자기 자리에 들여쓰여 남는다.
    expect(work.querySelector('li[data-task-id="t-2"]')?.className).toContain("child");
  });

  it("붙이고 뗄 수 있는지는 서버가 말한 대로 따른다", async () => {
    const { container, unmount } = renderPage();
    await screen.findByText("한빛의원 통합 마케팅", { selector: "h3" });
    expect(screen.getAllByText("참여 종료").length).toBe(2);
    expect(screen.getByLabelText("붙일 구성원")).toBeTruthy();
    unmount();
    cleanup();

    // 그 프로젝트를 읽기만 하는 사람에게는 관리 손잡이가 아예 나타나지 않는다 — 눌러야 아는 거절을 만들지 않는다.
    renderPage("jiho", { may_manage: false });
    await screen.findByText("한빛의원 통합 마케팅", { selector: "h3" });
    expect(screen.queryByText("참여 종료")).toBeNull();
    expect(screen.queryByLabelText("붙일 구성원")).toBeNull();
    expect(container).toBeTruthy();
  });

  it("붙어 있지 않은 사람에게는 업무가 왜 비어 있는지 말해 준다", async () => {
    renderPage("m-nobody", { tasks: [] });
    await screen.findByText("한빛의원 통합 마케팅", { selector: "h3" });
    expect(screen.getByText(/담당자에게만 보입니다/)).toBeTruthy();
  });

  it("담당자를 붙이면 그 프로젝트를 다시 읽는다", async () => {
    vi.mocked(api.assignToProject).mockResolvedValue({} as never);
    renderPage();
    await screen.findByText("한빛의원 통합 마케팅", { selector: "h3" });
    fireEvent.click(screen.getByLabelText("붙일 구성원"));
    fireEvent.click(screen.getByRole("option", { name: "한별" }));
    fireEvent.click(screen.getByText("참여로 붙이기"));
    await waitFor(() => expect(api.assignToProject).toHaveBeenCalledWith("p-1", { member_id: "m-han", kind: "member" }));
  });

  it("현재 참여자와 이력을 분리하고 선택 사유로 참여를 종료한다", async () => {
    vi.mocked(api.releaseFromProject).mockResolvedValue(undefined);
    renderPage();

    const current = await screen.findByRole("region", { name: "현재 참여자" });
    expect(within(current).getByText("지호")).toBeTruthy();
    const participationHistory = await screen.findByRole("region", { name: "참여 이력" });
    expect(within(participationHistory).getByText("1차 지원 종료")).toBeTruthy();

    fireEvent.click(within(current).getByRole("button", { name: "지호 참여 종료" }));
    fireEvent.change(screen.getByLabelText("참여 종료 사유 (선택)"), {
      target: { value: "고객사 지원 종료" },
    });
    fireEvent.click(screen.getByRole("button", { name: "종료 기록" }));

    await waitFor(() => {
      expect(api.releaseFromProject).toHaveBeenCalledWith("p-1", "jiho", "current-jiho", "고객사 지원 종료");
    });
  });

  it("프로젝트를 바꾸면 이전 프로젝트의 참여 종료 입력을 닫는다", async () => {
    vi.mocked(api.releaseFromProject).mockClear();
    const otherProject = { ...project, project_id: "p-2", name: "새 프로젝트" };
    vi.mocked(api.listProjects).mockResolvedValue([project, otherProject] as never);
    vi.mocked(api.getProject).mockImplementation(async (projectId) => ({
      ...detail,
      project_id: projectId,
      name: projectId === "p-1" ? project.name : otherProject.name,
    }) as never);
    render(<ProjectPage onError={vi.fn()} personaId="mina" />);

    const current = await screen.findByRole("region", { name: "현재 참여자" });
    fireEvent.click(within(current).getByRole("button", { name: "지호 참여 종료" }));
    expect(screen.getByLabelText("참여 종료 사유 (선택)")).toBeTruthy();

    fireEvent.click(screen.getByText("새 프로젝트", { selector: ".project-row-name" }).closest("button")!);
    await screen.findByText("새 프로젝트", { selector: "h3" });
    expect(screen.queryByLabelText("참여 종료 사유 (선택)")).toBeNull();
    expect(api.releaseFromProject).not.toHaveBeenCalled();
  });
});
