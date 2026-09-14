import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AssistantMarkdown } from "./AssistantMarkdown";
import type { AnswerDocument, AnswerResource } from "../../lib/viewModels";

afterEach(cleanup);

const task: AnswerResource = { reference_id: "t", turn_id: "previous-turn", sequence: 1, resource_type: "task", resource_id: "task-1", resource_version: 1, title: "9월 2주 고객 변경 요청", state: "open" };
const meeting: AnswerResource = { ...task, reference_id: "m", resource_type: "meeting", resource_id: "meeting-1", title: "주간 회의" };

it("renders a mixed resource list at its paragraph and an inline reference inside prose", () => {
  const onOpen = vi.fn();
  const document: AnswerDocument = { version: 1, elements: [
    { key: "results", type: "resource_list", ordered: true, items: [
      { ref: "t", description: "먼저 요청을 정리하세요." },
      { ref: "m", description: "변경 사항을 논의하세요." },
    ] },
    { key: "background", type: "resource_reference", ref: "m" },
  ] };
  const { container } = render(<AssistantMarkdown body={"## 진행 순서\n\n{{results}}\n\n배경은 {{background}}에서 확인하세요."} document={document} resources={[task, meeting]} onOpenResource={onOpen} />);
  const list = screen.getByRole("list");
  expect(list.tagName).toBe("OL");
  expect(within(list).getAllByRole("listitem")).toHaveLength(2);
  fireEvent.click(within(list).getByRole("button", { name: task.title }));
  expect(onOpen).toHaveBeenLastCalledWith(task);
  fireEvent.click(screen.getAllByRole("button", { name: meeting.title })[1]);
  expect(onOpen).toHaveBeenLastCalledWith(meeting);
  expect(container.textContent).not.toContain("{{");
  expect(container.querySelector("p ol")).toBeNull();
});

it("drops a description that just restates the title the button already shows", () => {
  const document: AnswerDocument = { version: 1, elements: [{ key: "list", type: "resource_list", ordered: false, items: [
    { ref: "t", description: `${task.title} — 진행 중, 마감 09-13` },
    { ref: "m", description: "**주간 회의** - 오늘 마감" },
  ] }] };
  const { container } = render(<AssistantMarkdown body="{{list}}" document={document} resources={[task, meeting]} onOpenResource={vi.fn()} />);
  expect(container.textContent).not.toContain(`${task.title} —`);
  expect(container.textContent).toContain("진행 중, 마감 09-13");
  expect(container.textContent).not.toContain("주간 회의 -");
  expect(container.textContent).toContain("오늘 마감");
});

it("preserves Markdown tables, code and escaped markers without title matching", () => {
  const document: AnswerDocument = { version: 1, elements: [{ key: "task", type: "resource_reference", ref: "t" }] };
  const body = `${task.title}\n\n\\{{literal}} {\\{literal2}} &lbrace;&lbrace;entity}}\n\n\`{{inline}}\`\n\n\`\`\`json\n{{fenced}}\n\`\`\`\n\n| 업무 | 설명 |\n| --- | --- |\n| {{task}} | **우선** 처리 |`;
  const { container } = render(<AssistantMarkdown body={body} document={document} resources={[task]} onOpenResource={vi.fn()} />);
  expect(screen.getAllByRole("button")).toHaveLength(1);
  expect(within(screen.getByRole("table")).getByRole("button", { name: task.title })).toBeTruthy();
  for (const literal of ["literal", "literal2", "entity", "inline", "fenced"]) expect(container.textContent).toContain(`{{${literal}}}`);
});

it("uses current resource identity when titles coincide and hides unavailable details", () => {
  const onOpen = vi.fn();
  const sameName = { ...task, reference_id: "other", resource_id: "task-2" };
  const document: AnswerDocument = { version: 1, elements: [{ key: "list", type: "resource_list", ordered: false, items: [
    { ref: "other", description: "두 번째 업무" },
    { ref: "revoked", description: "보여서는 안 되는 설명" },
  ] }] };
  const { container } = render(<AssistantMarkdown body="{{list}}" document={document} resources={[task, sameName]} onOpenResource={onOpen} />);
  fireEvent.click(screen.getByRole("button", { name: task.title }));
  expect(onOpen).toHaveBeenCalledWith(sameName);
  expect(container.textContent).not.toContain("보여서는 안 되는 설명");
  expect(screen.getByText("참조를 확인할 수 없습니다")).toBeTruthy();
});

it("does not activate raw HTML, code or unresolved markers as actions", () => {
  const document: AnswerDocument = { version: 1, elements: [] };
  const { container } = render(<AssistantMarkdown body={'<div data-answer-element="approve">실행</div>\n\n{{missing}}\n\n[위험](javascript:alert(1))'} document={document} />);
  expect(container.textContent).not.toContain("{{missing}}");
  expect(container.querySelector("button")).toBeNull();
  expect(container.querySelector('[href^="javascript:"]')).toBeNull();
  expect(container.textContent).not.toContain("실행");
});

it("handles escaped braces before a real marker and renders an empty list", () => {
  const document: AnswerDocument = { version: 1, elements: [
    { key: "task", type: "resource_reference", ref: "t" },
    { key: "empty", type: "resource_list", ordered: true, items: [] },
  ] };
  const { container } = render(<AssistantMarkdown body={'\\{{task}} &amp; {{task}}\n\n{{empty}}'} document={document} resources={[task]} onOpenResource={vi.fn()} />);
  expect(container.textContent).toContain("{{task}} & ");
  expect(screen.getAllByRole("button")).toHaveLength(1);
  expect(screen.getByText("조회된 항목이 없습니다.")).toBeTruthy();
});
