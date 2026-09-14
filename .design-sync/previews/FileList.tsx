import { FileList } from "ax-workspace-frontend";

export default { title: "General/FileList", component: FileList };

/**
 * 파일 한 줄씩의 목록 — 이름 · 크기 · (안 된 이유) · 빼는 자리.
 * **못 붙은 줄은 지우지 않는다** — 이름을 흐리게 두고 사유를 옆에 남긴다 (SPEC-004 §10).
 */
export const Attached = () => (
  <div className="surface-card" style={{ width: 560 }}>
    <FileList
      label="회의 자료"
      rows={[
        { key: "1", name: "2026-09-13 주간 업무보고.pdf", size: "1.2MB", removeLabel: "빼기", onRemove: () => {} },
        { key: "2", name: "회의실 예약 정책 초안.md", size: "8KB", removeLabel: "빼기", onRemove: () => {} },
        { key: "3", name: "액션 아이템 정리.xlsx", size: "220KB", removeLabel: "빼기", onRemove: () => {} },
      ]}
    />
  </div>
);

/** 못 붙은 줄 — 이름이 흐려지고 사유가 옆에 선다 */
export const WithFailure = () => (
  <div className="surface-card" style={{ width: 620 }}>
    <FileList
      label="자료 추출 결과"
      rows={[
        { key: "1", name: "정책서 v3.pdf", size: "3.4MB", removeLabel: "빼기", onRemove: () => {} },
        { key: "2", name: "암호걸린-계약서.pdf", size: "1.1MB", reason: "PDF 암호가 걸려 있습니다", removeLabel: "빼기", onRemove: () => {} },
        { key: "3", name: "회의녹취-원본.wav", size: "148MB", reason: "한 건당 20MB 를 넘습니다", removeLabel: "빼기", onRemove: () => {} },
      ]}
    />
  </div>
);

/** 여는 줄 — `onOpen` 을 주면 이름이 눌러진다. `active` 는 지금 열려 있는 줄 */
export const Openable = () => (
  <div className="surface-card" style={{ width: 560 }}>
    <FileList
      label="첨부 자료"
      rows={[
        { key: "1", name: "2026-09-13 주간 업무보고.pdf", size: "1.2MB", removeLabel: "빼기", onOpen: () => {} },
        { key: "2", name: "회의실 예약 정책 초안.md", size: "8KB", removeLabel: "빼기", onOpen: () => {}, active: true },
        { key: "3", name: "액션 아이템 정리.xlsx", size: "220KB", removeLabel: "빼기", onOpen: () => {} },
      ]}
    />
  </div>
);
