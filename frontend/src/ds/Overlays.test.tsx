import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it } from "vitest";
import { Drawer, ConfirmModal, Modal, Toast } from "./Modal";
import { Checkbox } from "./FormControls";
import { FileList } from "./FileList";
import { TimeChip } from "./TimeChip";

/* 바퀴 3b 가 폼·오버레이 부품을 새 DS 껍데기로 옮겼다. 껍데기를 갈면 «이름» 이 조용히 사라지기 쉽다 —
   클래스만 바뀌었으니 눈으로는 멀쩡해 보이고, 클래스 선택자로 쓴 테스트도 그대로 통과한다.
   그래서 여기서는 클래스가 아니라 **역할과 접근성 이름** 으로만 찾는다. 다음 바퀴들이 이 위에 얹힌다. */
it("옮긴 부품의 접근성 이름·역할이 그대로 있다", () => {
  const { unmount } = render(
    <Drawer closeLabel="상세 닫기" footer={<span className="scax-drawer__spacer" />} label="자료 상세" onClose={() => {}} title="계약 초안">
      <p>본문</p>
    </Drawer>,
  );
  const dialog = screen.getByRole("dialog", { name: "자료 상세" });
  expect(dialog.getAttribute("aria-modal")).toBe("true");
  expect(dialog.className).toContain("scax-drawer--lg");
  // 닫기 단추의 이름이 IconButton 으로 옮겨 가면서도 살아 있다
  expect(within(dialog).getByRole("button", { name: "상세 닫기" })).toBeTruthy();
  unmount();

  render(<ConfirmModal cancelLabel="돌아가기" closeLabel="닫기" confirmLabel="지우기" description="되돌릴 수 없습니다" onClose={() => {}} onConfirm={() => {}} title="지울까요" />);
  const alert = screen.getByRole("alertdialog", { name: "지울까요" });
  expect(within(alert).getByRole("button", { name: "지우기" })).toBeTruthy();
  expect(within(alert).getByRole("button", { name: "돌아가기" })).toBeTruthy();

  render(<Toast closeLabel="알림 지우기" message="저장했습니다" onClose={() => {}} />);
  expect(screen.getByRole("status").textContent).toContain("저장했습니다");

  // 체크박스: label 로 찾을 수 있고 disabled 가 살아 있다
  render(<Checkbox checked={false} disabled onChange={() => {}}>보고서 검토</Checkbox>);
  const box = screen.getByLabelText("보고서 검토") as HTMLInputElement;
  expect(box.type).toBe("checkbox");
  expect(box.disabled).toBe(true);

  render(<FileList label="붙인 자료" rows={[{ key: "a", name: "초안.pdf", onRemove: () => {}, removeLabel: "초안 빼기" }]} />);
  const list = screen.getByRole("list", { name: "붙인 자료" });
  expect(within(list).getByRole("button", { name: "초안 빼기" })).toBeTruthy();

  render(<TimeChip label="12:03" onClick={() => {}} />);
  expect(screen.getByRole("button", { name: "12:03" }).className).toBe("scax-time-chip");
});

/* 바퀴 6bc — 가운데 서는 모달 골격. 서랍과 같은 자리를 받고 폭만 부르는 쪽이 정한다.
   닫는 길 셋(× · Esc · 바깥 클릭)이 다 살아 있어야 한다 (R-3 과 같은 규칙이다). */
it("Modal 은 폭을 부르는 쪽이 정하고 닫는 길을 셋 다 갖는다", () => {
  const closes: string[] = [];
  const { unmount } = render(
    <Modal closeLabel="닫기" label="업무 요청" onClose={() => closes.push("x")} size="md" title="업무 요청">
      <p>본문</p>
    </Modal>,
  );
  const dialog = screen.getByRole("dialog", { name: "업무 요청" });
  expect(dialog.getAttribute("aria-modal")).toBe("true");
  // 560 — 회의록의 승격이 여는 폭이다
  expect(dialog.className).toContain("scax-modal--md");
  fireEvent.click(within(dialog).getByRole("button", { name: "닫기" }));
  fireEvent.keyDown(window, { key: "Escape" });
  fireEvent.mouseDown(dialog.parentElement as HTMLElement);
  expect(closes).toHaveLength(3);
  unmount();

  // 폭을 안 주면 DS 기본(880)이다 — 업무·오늘 화면의 생성 자리가 이것을 쓴다
  render(
    <Modal closeLabel="닫기" label="새 업무 추가" onClose={() => {}} title="새 업무 추가">
      <p>본문</p>
    </Modal>,
  );
  expect(screen.getByRole("dialog", { name: "새 업무 추가" }).className).toBe("scax-modal");
});

/* 바퀴 6bc — 갈래를 두지 않는 확인 모달(T08). 푸터가 하나로 줄어도 닫을 길은 머리의 × 로 남는다. */
it("ConfirmModal 은 취소를 접어도 닫는 자리를 잃지 않는다", () => {
  render(
    <ConfirmModal
      cancelLabel={null}
      closeLabel="닫기"
      confirmLabel="회의 취소"
      danger
      description="되돌릴 수 없습니다"
      onClose={() => {}}
      onConfirm={() => {}}
      title="회의를 취소할까요?"
    />,
  );
  const alert = screen.getByRole("alertdialog", { name: "회의를 취소할까요?" });
  expect(within(alert).queryByRole("button", { name: "돌아가기" })).toBeNull();
  expect(within(alert).getByRole("button", { name: "닫기" })).toBeTruthy();
  expect(within(alert).getByRole("button", { name: "회의 취소" })).toBeTruthy();
});
