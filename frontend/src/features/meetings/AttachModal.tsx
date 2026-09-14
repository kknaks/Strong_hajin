import { useState } from "react";

import { Button } from "../../ds/Button";
import { ApiError, attachMeetingMaterials } from "../../lib/api";
import { DropZone } from "../../ds/DropZone";
import { FileList, type FileRow } from "../../ds/FileList";
import { Icon } from "../../ds/icons/Icon";
import { Toast, useEscape } from "../../ds/Modal";
import { meetingScreen } from "../../lib/labels";
import type { MeetingMaterialFailure } from "../../lib/viewModels";

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
 *
 * **이번 바퀴 — 시안 12 로 맞췄다.**
 *   · 안내 한 줄 + 보라 [파일 추가]가 «한 줄» 이고, 고른 파일 목록이 그 아래 «같은 칸 안» 에 선다.
 *     예전에는 칸 안에 긴 [파일 선택] 단추가 통째로 누웠고 목록은 칸 밖에 따로 섰다.
 *   · 끌어다 놓는 동안 칸 전체가 연보라 바닥 + 보라 점선이 된다 — `DropZone` 이 갖는다.
 *   · 목록에서 한 건을 빼면 **뺀 뒤에** 검은 알림 한 줄이 뜬다 (시안 14). 되돌리기는 두지 않는다 —
 *     빼기는 «아직 안 보낸» 목록에서 지우는 일이라 되돌릴 command 가 서버에 없다.
 *
 * **받는 형식과 크기는 계약이 정한다** — PDF · Markdown · 한 건 20MB
 * (`backend/.../meetings/materials.py`). 시안에 URL 줄이 있어도 여기에 만들지 않는다.
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
  /** 「뺐다」는 알림. **실제로 목록에서 사라진 뒤**에만 선다 — 누른 사실이 아니라 결과를 말한다. */
  const [removedNotice, setRemovedNotice] = useState<string | null>(null);

  /* 고른 것이 하나도 없으면 묻지 않는다 — 물어볼 «잃을 것» 이 없다.
     남아 있으면 「아직 안 붙었다」는 사실을 문구가 직접 말한다. */
  const close = () => (staged.length > 0 ? setAskDiscard(true) : onClose());
  useEscape(close, !askDiscard);

  const usable = staged.filter((one) => one.reason === null);
  const rejected = staged.some((one) => one.reason !== null);

  function dropStaged(name: string) {
    setStaged((current) => {
      const next = current.filter((row) => row.file.name !== name);
      // 정말 빠졌을 때만 말한다 — 같은 이름이 없어 아무것도 안 빠졌으면 알림도 없다
      if (next.length !== current.length) setRemovedNotice(meetingScreen.fileRemoved);
      return next;
    });
  }

  const rows: FileRow[] = staged.map((one) => ({
    key: one.file.name,
    name: one.file.name,
    size: sizeText(one.file.size),
    reason: one.reason ? failureText(one.reason) : null,
    removeLabel: meetingScreen.attachDropFile,
    onRemove: () => dropStaged(one.file.name),
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
              drop={meetingScreen.attachDrop}
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
              {/* 시안 12: 고른 파일은 칸 «안» 에 줄로 선다 */}
              {staged.length > 0 && <FileList label={meetingScreen.attachTitle} rows={rows} />}
            </DropZone>

            {rejected && <div className="scax-field__error">{meetingScreen.attachPartial}</div>}
            {error && <div className="scax-field__error">{error}</div>}
          </div>
          <footer className="modal-foot">
            <Button variant="solid" tone="primary" disabled={usable.length === 0 || busy} onClick={() => void send()} type="button">
              {meetingScreen.attachSubmit}
            </Button>
          </footer>
        </section>
      </div>

      {/* 시안 14 — 검은 바닥 · 휴지통 글리프 · 문구. 읽어 주는 자리(role="status")는 부품이 갖는다 */}
      {removedNotice && (
        <Toast closeLabel="알림 지우기" icon="trash" message={removedNotice} onClose={() => setRemovedNotice(null)} />
      )}

      {askDiscard && (
        <div className="modal-backdrop" style={{ zIndex: 60 }}>
          <section aria-label={meetingScreen.attachDiscardTitle} aria-modal="true" className="modal" role="alertdialog">
            <header className="modal-head">
              <h3>{meetingScreen.attachDiscardTitle}</h3>
            </header>
            <footer className="modal-foot">
              <Button variant="text" onClick={() => setAskDiscard(false)} type="button">
                {meetingScreen.keep}
              </Button>
              <Button variant="solid" tone="danger" onClick={onClose} type="button">
                {meetingScreen.discard}
              </Button>
            </footer>
          </section>
        </div>
      )}
    </>
  );
}
