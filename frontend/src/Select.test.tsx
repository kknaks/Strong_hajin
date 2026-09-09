import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MultiSelect, Select } from "./Select";
import type { SelectOption } from "./Select";

afterEach(cleanup);

const PEOPLE: SelectOption[] = [
  { value: "kim", label: "김서연", description: "플랫폼", group: "개발" },
  { value: "lee", label: "이준호", description: "데이터", group: "개발" },
  { value: "park", label: "박민지", group: "디자인" },
  { value: "old", label: "떠난 사람", disabled: true, group: "디자인" },
];

/** 8개를 넘겨야 검색창이 저절로 붙는다 — 그 경계를 넘기는 목록. */
const MANY: SelectOption[] = Array.from({ length: 9 }, (_, index) => ({ value: `v${index}`, label: `항목 ${index}` }));

function open(name: string) {
  fireEvent.click(screen.getByLabelText(name));
}

describe("Select", () => {
  it("네이티브 select 가 아니라 팝오버 위의 listbox 다", () => {
    render(<Select label="담당자" onChange={vi.fn()} options={PEOPLE} value="lee" />);
    expect(document.querySelector("select")).toBeNull();
    const trigger = screen.getByLabelText("담당자");
    expect(trigger.className).toBe("select-trigger");
    expect(trigger.textContent).toContain("이준호");
    expect(screen.queryByRole("listbox")).toBeNull();

    open("담당자");
    expect(screen.getByRole("listbox", { name: "담당자" })).toBeTruthy();
    expect(screen.getAllByRole("option").length).toBe(PEOPLE.length);
  });

  it("값이 없으면 placeholder 가 서고, 고르면 값만 넘어온다", () => {
    const onChange = vi.fn();
    render(<Select label="담당자" onChange={onChange} options={PEOPLE} placeholder="담당자 고르기" value="" />);
    expect(screen.getByLabelText("담당자").textContent).toContain("담당자 고르기");

    open("담당자");
    fireEvent.click(screen.getByRole("option", { name: /김서연/ }));
    expect(onChange).toHaveBeenCalledWith("kim");
    // 하나 고르면 닫힌다
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("그룹은 값이 바뀔 때 한 번만 머리글을 낸다", () => {
    render(<Select label="담당자" onChange={vi.fn()} options={PEOPLE} value="" />);
    open("담당자");
    expect(screen.getAllByText("개발").length).toBe(1);
    expect(screen.getAllByText("디자인").length).toBe(1);
  });

  it("옵션이 8개를 넘으면 검색창이 저절로 붙고, 걸리는 것이 없으면 Empty(filter) 가 뜬다", () => {
    render(<Select label="항목" onChange={vi.fn()} options={MANY} value="" />);
    open("항목");
    const search = screen.getByLabelText("검색");
    expect(screen.getAllByRole("option").length).toBe(MANY.length);

    fireEvent.change(search, { target: { value: "항목 3" } });
    expect(screen.getAllByRole("option").length).toBe(1);

    fireEvent.change(search, { target: { value: "없는 것" } });
    expect(screen.queryAllByRole("option").length).toBe(0);
    expect(screen.getByText("조건에 맞는 항목이 없습니다")).toBeTruthy();
    // 필터 초기화는 Empty variant="filter" 가 데려온다
    fireEvent.click(screen.getByRole("button", { name: "필터 초기화" }));
    expect(screen.getAllByRole("option").length).toBe(MANY.length);
  });

  it("8개 이하면 검색창이 없다", () => {
    render(<Select label="담당자" onChange={vi.fn()} options={PEOPLE} value="" />);
    open("담당자");
    expect(screen.queryByLabelText("검색")).toBeNull();
  });

  it("↑↓ 로 옮기고 Enter 로 고른다 — 비활성 항목은 건너뛴다", () => {
    const onChange = vi.fn();
    render(<Select label="담당자" onChange={onChange} options={PEOPLE} value="" />);
    open("담당자");
    const list = screen.getByRole("listbox");
    expect(list.getAttribute("aria-activedescendant")).toBe(screen.getByRole("option", { name: /김서연/ }).id);

    fireEvent.keyDown(list, { key: "ArrowDown" });
    fireEvent.keyDown(list, { key: "ArrowDown" });
    // 네 번째는 disabled 라 더 내려가지 않는다
    fireEvent.keyDown(list, { key: "ArrowDown" });
    fireEvent.keyDown(list, { key: "Enter" });
    expect(onChange).toHaveBeenCalledWith("park");
  });

  it("검색창이 없으면 타이핑으로 항목을 짚는다", () => {
    const onChange = vi.fn();
    render(<Select label="담당자" onChange={onChange} options={PEOPLE} value="" />);
    open("담당자");
    const list = screen.getByRole("listbox");
    fireEvent.keyDown(list, { key: "박" });
    fireEvent.keyDown(list, { key: "Enter" });
    expect(onChange).toHaveBeenCalledWith("park");
  });

  it("footerAction 은 목록 바닥의 btn link 다", () => {
    const onAction = vi.fn();
    render(<Select footerAction={{ label: "새 프로젝트로 추가", onAction }} label="프로젝트" onChange={vi.fn()} options={PEOPLE} value="" />);
    open("프로젝트");
    const action = screen.getByRole("button", { name: "새 프로젝트로 추가" });
    expect(action.className).toBe("btn link");
    fireEvent.click(action);
    expect(onAction).toHaveBeenCalled();
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("트리거는 render prop 으로 갈아 낀다 — 툴바의 filter-chip", () => {
    render(
      <Select
        label="정렬"
        onChange={vi.fn()}
        options={[{ value: "new", label: "최신순" }]}
        trigger={({ label, props }) => (
          <button {...props} className="filter-chip">
            정렬 · {label}
          </button>
        )}
        value="new"
      />,
    );
    const trigger = screen.getByLabelText("정렬");
    expect(trigger.className).toBe("filter-chip");
    expect(trigger.textContent).toContain("최신순");
  });

  it("에러는 FieldMessage 로 한 줄만 나가고 보더가 갈아입는다", () => {
    render(<Select error="담당자를 고르세요" help="같은 부서 사람만 보입니다" label="담당자" onChange={vi.fn()} options={PEOPLE} value="" />);
    expect(screen.getByLabelText("담당자").getAttribute("aria-invalid")).toBe("true");
    expect(screen.getByRole("alert").textContent).toBe("담당자를 고르세요");
    expect(screen.queryByText("같은 부서 사람만 보입니다")).toBeNull();
  });
});

describe("MultiSelect", () => {
  it("고를 때마다 닫히지 않고, 값은 배열로 온다", () => {
    const onChange = vi.fn();
    render(<MultiSelect label="참석자" onChange={onChange} options={PEOPLE} value={["kim"]} />);
    open("참석자");
    fireEvent.click(screen.getByRole("option", { name: /이준호/ }));
    expect(onChange).toHaveBeenCalledWith(["kim", "lee"]);
    expect(screen.getByRole("listbox")).toBeTruthy();
  });

  it("이미 고른 것을 다시 누르면 빠진다", () => {
    const onChange = vi.fn();
    render(<MultiSelect label="참석자" onChange={onChange} options={PEOPLE} value={["kim", "lee"]} />);
    open("참석자");
    fireEvent.click(screen.getByRole("option", { name: /김서연/ }));
    expect(onChange).toHaveBeenCalledWith(["lee"]);
  });

  it("칩은 두 개까지 서고 나머지는 +N 으로 접힌다", () => {
    render(<MultiSelect label="참석자" onChange={vi.fn()} options={PEOPLE} value={["kim", "lee", "park"]} />);
    const trigger = screen.getByLabelText("참석자");
    expect(trigger.querySelectorAll(".badge").length).toBe(2);
    expect(trigger.textContent).toContain("+1");
  });

  it("전체 선택은 고를 수 있는 것만 데려가고, 한 번 더 누르면 전부 풀린다", () => {
    const onChange = vi.fn();
    const { rerender } = render(<MultiSelect label="참석자" onChange={onChange} options={PEOPLE} value={[]} />);
    open("참석자");
    fireEvent.click(screen.getByRole("button", { name: "전체 선택" }));
    expect(onChange).toHaveBeenCalledWith(["kim", "lee", "park"]);

    rerender(<MultiSelect label="참석자" onChange={onChange} options={PEOPLE} value={["kim", "lee", "park"]} />);
    fireEvent.click(screen.getByRole("button", { name: "전체 해제" }));
    expect(onChange).toHaveBeenLastCalledWith([]);
  });

  it("지우기는 값이 있을 때만 뜨고 한 번에 비운다", () => {
    const onChange = vi.fn();
    const { rerender } = render(<MultiSelect label="참석자" onChange={onChange} options={PEOPLE} value={[]} />);
    expect(screen.queryByRole("button", { name: "지우기" })).toBeNull();

    rerender(<MultiSelect label="참석자" onChange={onChange} options={PEOPLE} value={["kim"]} />);
    fireEvent.click(screen.getByRole("button", { name: "지우기" }));
    expect(onChange).toHaveBeenCalledWith([]);
  });

  it("옵션의 체크는 Checkbox 와 같은 네모이고, 목록은 다중 선택으로 읽힌다", () => {
    render(<MultiSelect label="참석자" onChange={vi.fn()} options={PEOPLE} value={["kim"]} />);
    open("참석자");
    expect(screen.getByRole("listbox").getAttribute("aria-multiselectable")).toBe("true");
    const checked = screen.getByRole("option", { name: /김서연/ });
    expect(checked.querySelector(".checkbox-box input")?.hasAttribute("checked")).toBe(true);
  });
});
