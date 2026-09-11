import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DateField, parseDateInput } from "./DateField";

describe("date input parsing", () => {
  it("accepts what people actually type and refuses a date the calendar does not have", () => {
    expect(parseDateInput("2026/09/30")).toBe("2026-09-30");
    expect(parseDateInput("2026-09-30")).toBe("2026-09-30");
    expect(parseDateInput("2026.09.30")).toBe("2026-09-30");
    expect(parseDateInput("20260930")).toBe("2026-09-30");
    expect(parseDateInput("2026/2/3")).toBeNull(); // needs the padded form
    expect(parseDateInput("2026-02-31")).toBeNull();
    expect(parseDateInput("")).toBeNull();
  });
});

describe("date field", () => {
  afterEach(cleanup);

  it("shows the stored ISO value as YYYY/MM/DD regardless of the browser locale", () => {
    render(<DateField id="due" label="기한" onChange={vi.fn()} value="2026-09-30" />);
    const field = screen.getByLabelText("기한") as HTMLInputElement;
    // A native date input would render this in the browser's locale; this one is ours.
    expect(field.getAttribute("type")).toBe("text");
    expect(field.value).toBe("2026/09/30");
    expect(field.placeholder).toBe("YYYY/MM/DD");
    expect(document.body.textContent).not.toMatch(/mm\/dd\/yyyy/i);
  });

  it("can use the dotted task-card presentation without changing the ISO boundary", () => {
    const onChange = vi.fn();
    render(
      <DateField
        displaySeparator="."
        id="task-due"
        label="기한"
        onChange={onChange}
        pickerIcon="chevron-down"
        value="2026-09-30"
      />,
    );
    const field = screen.getByLabelText("기한") as HTMLInputElement;
    expect(field.value).toBe("2026.09.30");
    expect(field.placeholder).toBe("YYYY.MM.DD");
    fireEvent.change(field, { target: { value: "2026.10.15" } });
    expect(onChange).toHaveBeenCalledWith("2026-10-15");
    expect(screen.getByRole("button", { name: "기한 달력 열기" }).querySelector("svg")).toBeTruthy();
  });

  it("hands the caller ISO, never the display string", () => {
    const onChange = vi.fn();
    render(<DateField id="due" label="기한" onChange={onChange} value="" />);
    const field = screen.getByLabelText("기한");

    fireEvent.change(field, { target: { value: "2026/10/15" } });
    expect(onChange).toHaveBeenCalledWith("2026-10-15");

    // A half-typed value is not pushed across the boundary as a broken date.
    onChange.mockClear();
    fireEvent.change(field, { target: { value: "2026/1" } });
    expect(onChange).not.toHaveBeenCalled();

    // Clearing the field clears the value.
    fireEvent.change(field, { target: { value: "" } });
    expect(onChange).toHaveBeenCalledWith("");
  });

  it("keeps a platform calendar reachable by keyboard and screen reader", () => {
    const showPicker = vi.fn();
    render(<DateField id="due" label="기한" onChange={vi.fn()} value="2026-09-30" />);
    const trigger = screen.getByRole("button", { name: "기한 달력 열기" });
    const native = document.querySelector('input[type="date"]') as HTMLInputElement;
    expect(native.value).toBe("2026-09-30"); // the picker is bound to the same ISO value
    native.showPicker = showPicker;
    fireEvent.click(trigger);
    expect(showPicker).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText("기한").getAttribute("aria-describedby")).toBeTruthy();
  });

  it("restores the formatted value when an unfinished edit loses focus", () => {
    render(<DateField id="due" label="기한" onChange={vi.fn()} value="2026-09-30" />);
    const field = screen.getByLabelText("기한") as HTMLInputElement;
    fireEvent.change(field, { target: { value: "2026/9" } });
    expect(field.value).toBe("2026/9");
    fireEvent.blur(field);
    expect(field.value).toBe("2026/09/30");
  });
});
