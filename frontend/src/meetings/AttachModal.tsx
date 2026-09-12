import { useState } from "react";

import { ApiError, attachMeetingMaterials } from "../api";
import { DropZone } from "../DropZone";
import { FileList, type FileRow } from "../FileList";
import { Icon } from "../Icon";
import { useEscape } from "../Modal";
import { meetingScreen } from "../labels";
import type { MeetingMaterialFailure } from "../viewModels";

const MAX_BYTES = 20 * 1024 * 1024;
const ALLOWED = [".pdf", ".md", ".markdown"];

export function sizeText(bytes: number): string {
  return bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)}MB` : `${Math.max(1, Math.round(bytes / 1024))}KB`;
}

/** 서버가 낸 사유를 확정 문구로 — 값은 계약이고 문장은 화면이 소유한다. */
export function failureText(reason: MeetingMaterialFailure["reason"]): string {
  return reason === "too_large" ? meetingScreen.attachTooBig : meetingScreen.attachBadKind;
}

/**
 * 고르는 시점의 빠른 판정 — 24MB 짜리를 다 올리고 나서 「크다」는 말을 듣지 않게 한다.
 * **판정의 정본은 서버다**: 여기서 통과한 것도 서버가 되돌릴 수 있고 그 사유가 같은 자리에 선다.
 */
function localReason(file: File): MeetingMaterialFailure["reason"] | null {
  const lower = file.name.toLowerCase();
  if (!ALLOWED.some((extension) => lower.endsWith(extension))) return "unsupported_type";
  if (file.size > MAX_BYTES) return "too_large";
  return null;
}

type Staged = { file: File; reason: MeetingMaterialFailure["reason"] | null };

/**
 * MOD-104 자료 첨부 — 붙는 자리는 회의 하나라 어디에 붙일지를 묻지 않는다 (X-117).
 *
 * 여럿을 한 번에 놓으면 **되는 것만 붙고 안 되는 것은 사유와 함께 남는다** (SPEC §10).
 * 「진행 중」에는 이 자리가 열리지 않는다 — 서버도 409 로 막는다.
 */
export function AttachModal({
  meetingId,
  onClose,
  onAttached,
}: {
  meetingId: string;
  onClose: () => void;
  /** 붙은 건수를 받아 목록을 다시 읽는 것은 부르는 쪽이 한다. */
  onAttached: (attachedCount: number, partial: boolean) => void;
}) {
  const [staged, setStaged] = useState<Staged[]>([]);
  const [askDiscard, setAskDiscard] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const close = () => (staged.length > 0 ? setAskDiscard(true) : onClose());
  useEscape(close, !askDiscard);

  const usable = staged.filter((one) => one.reason === null);
  const rejected = staged.some((one) => one.reason !== null);

  const rows: FileRow[] = staged.map((one) => ({
    key: one.file.name,
    name: one.file.name,
    size: sizeText(one.file.size),
    reason: one.reason ? failureText(one.reason) : null,
    removeLabel: meetingScreen.attachDropFile,
    onRemove: () => setStaged((current) => current.filter((row) => row.file.name !== one.file.name)),
  }));

  async function send() {
    if (usable.length === 0 || busy) return;
    setBusy(true);
    setError(null);
    try {
      const result = await attachMeetingMaterials(meetingId, usable.map((one) => one.file));
      if (result.failed.length > 0) {
        // 되돌아온 것만 남긴다 — 붙은 것은 이 목록에서 빠지고 못 붙은 것이 사유와 함께 남는다.
        const failedByName = new Map(result.failed.map((row) => [row.name, row.reason]));
        setStaged((current) =>
          current
            .filter((one) => failedByName.has(one.file.name))
            .map((one) => ({ ...one, reason: failedByName.get(one.file.name) ?? one.reason })),
        );
        onAttached(result.attached.length, true);
        setBusy(false);
        return;
      }
      onAttached(result.attached.length, false);
    } catch (reason) {
      // 한 건도 못 붙었다 — 사유가 함께 온다 (422 `meeting_materials_rejected`).
      const detail = reason instanceof ApiError ? (reason.detail as { code?: string; failed?: MeetingMaterialFailure[] } | undefined) : undefined;
      if (detail?.code === "meeting_materials_rejected" && detail.failed) {
        const failedByName = new Map(detail.failed.map((row) => [row.name, row.reason]));
        setStaged((current) => current.map((one) => ({ ...one, reason: failedByName.get(one.file.name) ?? one.reason })));
      } else {
        setError(reason instanceof Error ? reason.message : "자료를 붙이지 못했습니다.");
      }
      setBusy(false);
    }
  }

  return (
    <>
      <div className="modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && close()}>
        <section aria-label={meetingScreen.attachTitle} aria-modal="true" className="modal" role="dialog">
          <header className="modal-head">
            <h3>{meetingScreen.attachTitle}</h3>
            <button aria-label="닫기" className="modal-close" onClick={close} type="button">
              <Icon name="close" size={16} />
            </button>
          </header>
          <div className="modal-body">
            <DropZone
              accept=".pdf,.md,.markdown"
              disabled={busy}
              hint={meetingScreen.attachLimit}
              onFiles={(files) =>
                setStaged((current) => [
                  ...current,
                  ...files
                    .filter((file) => !current.some((one) => one.file.name === file.name))
                    .map((file) => ({ file, reason: localReason(file) })),
                ])
              }
              pickLabel={meetingScreen.attachPick}
            >
              <span className="t-meta" style={{ fontSize: 13 }}>
                {meetingScreen.attachDrop}
              </span>
            </DropZone>

            {rejected && <div className="field-error">{meetingScreen.attachPartial}</div>}
            {error && <div className="field-error">{error}</div>}
            {staged.length > 0 && <FileList label={meetingScreen.attachTitle} rows={rows} />}
          </div>
          <footer className="modal-foot">
            <button className="btn h40 primary" disabled={usable.length === 0 || busy} onClick={() => void send()} type="button">
              {meetingScreen.attachSubmit}
            </button>
          </footer>
        </section>
      </div>

      {askDiscard && (
        <div className="modal-backdrop" style={{ zIndex: 60 }}>
          <section aria-label={meetingScreen.discardTitle} aria-modal="true" className="modal" role="alertdialog">
            <header className="modal-head">
              <h3>{meetingScreen.discardTitle}</h3>
            </header>
            <footer className="modal-foot">
              <button className="btn h40 ghost" onClick={() => setAskDiscard(false)} type="button">
                {meetingScreen.keep}
              </button>
              <button className="btn h40 danger" onClick={onClose} type="button">
                {meetingScreen.discard}
              </button>
            </footer>
          </section>
        </div>
      )}
    </>
  );
}
