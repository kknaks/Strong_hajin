import { useState } from "react";

import { Button, IconButton } from "../../ds/Button";
import { addMeetingMemoLine } from "../../lib/api";
import { Composer } from "../chat/Composer";
import { Icon } from "../../ds/icons/Icon";
import { Select } from "../../ds/Select";
import { emptyActionLabel, meetingScreen, selectLabel } from "../../lib/labels";
import type { MeetingAgenda, MeetingLine } from "../../lib/viewModels";

/**
 * `E80` 대상 안건 + `E81` 메모 입력 — 회의록 아래 **한 줄 칸 하나**다 (SPEC §6-3).
 *
 * 안건마다 칸을 나누지 않는다: 회의 중에는 말이 안건을 오가므로 칸을 오가며 적으면 적는 속도가 말을 못 따라간다.
 * **저장 버튼을 두지 않는다** (§6-7) — 던지면 곧바로 간다. 실패하면 `T34` 로 알리고 **친 것은 칸에 그대로 둔다.**
 * 돌아온 줄만 화면에 붙인다 — 낙관 렌더를 하지 않는다.
 */
export function MemoComposer({
  meetingId,
  agendas,
  onSaved,
  onCreateAgenda,
}: {
  meetingId: string;
  agendas: MeetingAgenda[];
  onSaved: (agendaId: string, line: MeetingLine) => void;
  /** 회의 중에 안건을 세운다 — 선 안건을 돌려주면 그 자리에서 대상이 된다. */
  onCreateAgenda: (title: string) => Promise<MeetingAgenda>;
}) {
  const [target, setTarget] = useState("");
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  /* 메모를 적다 말고 안건을 세우는 중 — 같은 칸이 제목을 받는다. 자리를 옮기지 않는다 */
  const [naming, setNaming] = useState(false);
  const [draftAgenda, setDraftAgenda] = useState("");

  // 기본값은 안건 1 이다 (§6-4). 안건이 하나도 없으면 매달 자리가 없어 드롭다운이 비활성이다.
  const agendaId = agendas.some((agenda) => agenda.agenda_id === target) ? target : agendas[0]?.agenda_id ?? "";
  const empty = agendas.length === 0;

  async function createAgenda() {
    const title = draftAgenda.trim();
    if (!title || busy) return;
    setBusy(true);
    try {
      const created = await onCreateAgenda(title);
      // 세운 안건이 곧 대상이 된다 — 적으려던 메모가 그리로 붙는다
      setTarget(created.agenda_id);
      setDraftAgenda("");
      setNaming(false);
      setFailed(false);
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }

  async function send() {
    const text = draft.trim();
    if (!text || !agendaId || busy) return;
    setBusy(true);
    try {
      const line = await addMeetingMemoLine(meetingId, agendaId, text);
      onSaved(agendaId, line);
      setDraft("");
      setFailed(false);
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  }

  return (
    /* 껍데기 footer 를 벗었다. 상세가 이미 `.scax-note__composer` 를 세우고 있어서 footer 가 둘이었고,
       안쪽 것은 flex 자식인데 자라지 않아 «칸이 왼쪽에 짧게 몰려» 있었다(현재 화면 21). 여백도 두 겹이었다.
       이제 이 부품은 그 줄을 «채우는 쪽» 이다 — 자리와 테두리는 바깥 footer 가 갖는다. */
    <div className="scax-memo-composer">
      <Composer
        disabled={busy || (!naming && empty)}
        error={failed ? meetingScreen.memoSaveFailed : null}
        leading={
          naming ? (
            <>
              <span className="t-meta" style={{ flex: "none", paddingLeft: 4, fontSize: 13 }}>
                {meetingScreen.newAgenda}
              </span>
              <IconButton name="close" size={14} label={meetingScreen.cancel} onClick={() => { setNaming(false); setDraftAgenda(""); }} style={{ flex: "none", width: 30, height: 30, color: "var(--scax-color-ink-assistive)" }} />
            </>
          ) : (
            <Select
            emptyActionLabel={emptyActionLabel.filter}
            labels={selectLabel}
              footerAction={{ label: meetingScreen.newAgenda, onAction: () => setNaming(true) }}
              label={meetingScreen.agenda}
              onChange={setTarget}
              options={agendas.map((agenda, index) => ({ value: agenda.agenda_id, label: `${meetingScreen.agenda} ${index + 1}` }))}
              trigger={({ label, props }) => (
                <Button variant="text" size="sm" {...props} style={{ flex: "none", color: "var(--scax-color-ink-alt)" }} type="button">
                  {empty ? meetingScreen.memoTargetEmpty : label} <Icon name="chevron-down" size={12} />
                </Button>
              )}
              value={agendaId}
            />
          )
        }
        onChange={naming ? setDraftAgenda : setDraft}
        onSubmit={() => void (naming ? createAgenda() : send())}
        placeholder={naming ? meetingScreen.agendaPlaceholder : meetingScreen.memoPlaceholder}
        /* 시안 22 의 그 자리 — 칸 오른쪽 «밖» 의 보라 단추다 */
        sendVariant="button"
        sendLabel={naming ? meetingScreen.addAgenda : meetingScreen.recordMemo}
        value={naming ? draftAgenda : draft}
      />
    </div>
  );
}
