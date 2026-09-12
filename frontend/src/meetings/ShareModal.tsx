import { useEffect, useMemo, useState } from "react";

import { readMeetingShares, revokeMeetingShare, shareMeetingWith } from "../api";
import { Icon } from "../Icon";
import { useEscape } from "../Modal";
import { meetingScreen, personName } from "../labels";
import type { MeetingInfo, MeetingViewer } from "../viewModels";
import { OrgDirectory, PersonSearch, PickedTags } from "./PeoplePicker";
import { useRoster, type RosterPerson } from "./roster";

/**
 * MOD-105 공유 — 참석자가 아닌 사람에게 **읽기**만 연다 (X-31).
 *
 * 사람을 고르는 자리는 회의 예약(MOD-102)과 **같은 모양**이다 — 검색 · 조직도 · 볼 수 있는 사람 셋.
 * 같은 「사람을 고른다」인데 두 모달이 다르게 생기면 두 번 배워야 한다.
 *
 * 「볼 수 있는 사람」은 참석과 공유가 한 목록에 서고 `basis` 가 둘을 가른다 — **거둘 수 있는 것은 공유뿐이다**
 * (참석을 빼는 자리는 회의 정보 편집이고 서버도 409 로 막는다). **알림은 가지 않는다** (§2.2).
 */
export function ShareModal({ meeting, onClose, onNotice }: { meeting: MeetingInfo; onClose: () => void; onNotice: (message: string) => void }) {
  const [query, setQuery] = useState("");
  const [picks, setPicks] = useState<RosterPerson[]>([]);
  const [unitId, setUnitId] = useState<string | null>(null);
  const [viewers, setViewers] = useState<MeetingViewer[] | null>(null);
  const [askDiscard, setAskDiscard] = useState(false);
  const [askUnshare, setAskUnshare] = useState<MeetingViewer | null>(null);
  const [busy, setBusy] = useState(false);
  const { roster } = useRoster(true);

  useEffect(() => {
    let cancelled = false;
    void readMeetingShares(meeting.meeting_id)
      .then((next) => {
        if (!cancelled) setViewers(next);
      })
      .catch(() => {
        if (!cancelled) setViewers([]);
      });
    return () => {
      cancelled = true;
    };
  }, [meeting.meeting_id]);

  /* 이미 볼 수 있는 사람은 검색에도 조직도에도 안 낸다 — 참석이든 공유든 같다 (E02) */
  const excluded = useMemo(
    () => new Set([...(viewers ?? []).map((one) => one.member_id), ...picks.map((one) => one.member_id)]),
    [picks, viewers],
  );

  const close = () => (picks.length > 0 ? setAskDiscard(true) : onClose());
  useEscape(close, !askDiscard && !askUnshare);

  async function share() {
    if (picks.length === 0 || busy) return;
    setBusy(true);
    try {
      setViewers(await shareMeetingWith(meeting.meeting_id, picks.map((one) => one.member_id)));
      setPicks([]);
      onNotice(meetingScreen.shared);
      onClose();
    } finally {
      setBusy(false);
    }
  }

  async function unshare(viewer: MeetingViewer) {
    setBusy(true);
    try {
      setViewers(await revokeMeetingShare(meeting.meeting_id, viewer.member_id));
    } finally {
      setAskUnshare(null);
      setBusy(false);
    }
  }

  return (
    <>
      <div className="modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && close()}>
        <section aria-label={meetingScreen.shareTitle} aria-modal="true" className="modal meeting-modal-share" role="dialog">
          <header className="modal-head">
            <h3>{meetingScreen.shareTitle}</h3>
            <button aria-label="닫기" className="modal-close" onClick={close} type="button">
              <Icon name="close" size={16} />
            </button>
          </header>
          <div className="modal-body">
            <p style={{ fontSize: 13, color: "var(--text-secondary)" }}>{meetingScreen.shareLead}</p>

            <div className="field">
              <label>
                {meetingScreen.sharePeople} <span className="count-badge">{picks.length}</span>
              </label>
              <PickedTags
                items={picks.map((person) => ({
                  key: person.member_id,
                  name: person.name,
                  note: person.unit,
                  onRemove: () => setPicks((current) => current.filter((one) => one.member_id !== person.member_id)),
                }))}
              />
              <div style={{ marginTop: 8 }}>
                <PersonSearch
                  excluded={excluded}
                  onPick={(person) => {
                    setPicks((current) => [...current, person]);
                    setQuery("");
                  }}
                  onQueryChange={setQuery}
                  placeholder={meetingScreen.nameSearchPlaceholder}
                  query={query}
                  roster={roster}
                />
              </div>
              <div style={{ marginTop: 8 }}>
                <OrgDirectory
                  emptyText={meetingScreen.shareUnitEmpty}
                  excluded={excluded}
                  mode="add"
                  onToggle={(person) => setPicks((current) => [...current, person])}
                  onUnitChange={setUnitId}
                  roster={roster}
                  selected={new Set()}
                  unitId={unitId}
                />
              </div>
            </div>

            {/* E04 볼 수 있는 사람 · E05 [삭제] — 참석자에게는 안 붙는다. 빼는 자리는 [수정]이다 (X-134) */}
            <div className="field">
              <label>{meetingScreen.shareViewers}</label>
              <table className="plain-table meeting-share-table">
                <thead>
                  <tr>
                    <th>{meetingScreen.shareName}</th>
                    <th className="center">{meetingScreen.shareHow}</th>
                    <th className="end" />
                  </tr>
                </thead>
                <tbody>
                  {(viewers ?? []).map((viewer) => (
                    <tr key={`${viewer.basis}-${viewer.member_id}`}>
                      <td className="title-cell">
                        <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                          <span className="avatar xs">{personName(viewer.name).slice(0, 1)}</span>
                          {personName(viewer.name)}
                        </span>
                      </td>
                      <td className="center">
                        <span className={viewer.basis === "attendee" ? "badge neutral" : "badge outline"}>
                          {viewer.basis === "attendee" ? meetingScreen.shareAttending : meetingScreen.shareViewing}
                        </span>
                      </td>
                      <td className="end">
                        {/* 참석은 여기서 거둘 수 없다 — 빼는 자리는 [수정]이다 (X-134) */}
                        {viewer.basis === "share" && (
                          <button className="btn ghost h30" disabled={busy} onClick={() => setAskUnshare(viewer)} type="button">
                            {meetingScreen.remove}
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          <footer className="modal-foot">
            <button className="btn h40 primary" disabled={picks.length === 0 || busy} onClick={() => void share()} type="button">
              {meetingScreen.shareTitle}
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

      {askUnshare && (
        <div className="modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && setAskUnshare(null)} style={{ zIndex: 60 }}>
          <section aria-label={meetingScreen.unshareTitle} aria-modal="true" className="modal" role="alertdialog">
            <header className="modal-head">
              <h3>{meetingScreen.unshareTitle}</h3>
            </header>
            <footer className="modal-foot">
              <button className="btn h40 ghost" onClick={() => setAskUnshare(null)} type="button">
                {meetingScreen.keep}
              </button>
              <button
                className="btn h40 danger"
                disabled={busy}
                onClick={() => void unshare(askUnshare)}
                type="button"
              >
                {meetingScreen.remove}
              </button>
            </footer>
          </section>
        </div>
      )}
    </>
  );
}
