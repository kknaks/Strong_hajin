import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("./api", () => ({
  assignToProject: vi.fn(),
  createProject: vi.fn(),
  getMemberDirectory: vi.fn(),
  getOrganizationTree: vi.fn(),
  getProject: vi.fn(),
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
    { member_id: "mina", display_name: "민아", assignment_kind: "lead", valid_from: null, valid_until: null },
    { member_id: "jiho", display_name: "지호", assignment_kind: "member", valid_from: null, valid_until: null },
  ],
  tasks: [
    { task_id: "t-1", title: "한빛 9월 통합 마케팅", state: "open", start_date: null, due_date: "2026-10-03", parent_task_id: null },
    { task_id: "t-2", title: "홈페이지 디자인 기획", state: "done", start_date: null, due_date: "2026-09-12", parent_task_id: "t-1" },
  ],
};

function renderPage(personaId = "mina", overrides: Record<string, unknown> = {}) {
  vi.mocked(api.listProjects).mockResolvedValue([project] as never);
  vi.mocked(api.getProject).mockResolvedValue({ ...detail, ...overrides } as never);
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

    const people = container.querySelector('[aria-label="담당자"]') as HTMLElement;
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
    expect(screen.getAllByText("떼기").length).toBe(2);
    expect(screen.getByLabelText("붙일 구성원")).toBeTruthy();
    unmount();
    cleanup();

    // 그 프로젝트를 읽기만 하는 사람에게는 관리 손잡이가 아예 나타나지 않는다 — 눌러야 아는 거절을 만들지 않는다.
    renderPage("jiho", { may_manage: false });
    await screen.findByText("한빛의원 통합 마케팅", { selector: "h3" });
    expect(screen.queryByText("떼기")).toBeNull();
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
    fireEvent.change(screen.getByLabelText("붙일 구성원"), { target: { value: "m-han" } });
    fireEvent.click(screen.getByText("참여로 붙이기"));
    await waitFor(() => expect(api.assignToProject).toHaveBeenCalledWith("p-1", { member_id: "m-han", kind: "member" }));
  });
});
