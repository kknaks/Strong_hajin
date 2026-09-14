import { useEffect, useMemo, useState } from "react";

import { Button } from "../../ds/Button";
import { DateField } from "../../ds/DateField";
import { Icon } from "../../ds/icons/Icon";
import { Skeleton } from "../../ds/Skeleton";
import { TimeRangeField } from "../../ds/TimeField";
import { useEscape } from "../../ds/Modal";
import { readMeeting, updateMeetingInfo } from "../../lib/api";
import {
  datePickerLabel,
  emptyActionLabel,
  formatMonthLong,
  meetingClock,
  meetingDateInput,
  meetingIsoAt,
  meetingScreen,
  personName,
  selectLabel,
  seoulToday,
  timeFieldLabel,
  weekdayNames,
} from "../../lib/labels";
import type { MeetingRecord } from "../../lib/viewModels";
import { PersonSearch, PickedTags } from "./PeoplePicker";
import { useRoster, type RosterPerson } from "./roster";

/** 참석자 한 줄 — 상세가 쓰던 것과 같은 모양이다 (`MeetingInfo.attendees` 의 원소). */
type Attendee = { member_id: string; display_name: string };

type Head = { title: string; date: string; from: string; to: string; place: string; people: Attendee[] };

/**
 * 회의 정보 수정 — **목록 카드의 [수정]이 여는 모달** (시안 02).
 *
 * 예전에는 상세 머리의 「회의 정보」 옆 연필이 그 자리에서 폼을 펴서, 제목 줄 위에 구획 라벨이
 * 한 겹 얹히고 [회의 시작]이 아래 줄로 밀렸다(현재 화면 07·11). 시안의 상세 머리는 제목으로
 * 시작하므로 **고치는 자리를 목록 카드로 옮겼다** — 시안 02 의 카드에 이미 있던 그 [수정]이다.
 *
 * **값과 저장 경로는 한 줄도 안 바꿨다.** 칸 다섯(회의명 · 날짜 · 시작~종료 · 장소 · 참석자)도,
 * 「바뀐 것이 없으면 저장할 수 없다」도, `updateMeetingInfo` 로 나가는 patch 모양도 그대로다.
 *
 * **열 수 있는지는 서버가 말한다** — 목록 행에는 `can_edit_info` 가 없으므로 이 모달이 열리며
 * 상세를 읽고, 서버가 「못 고친다」고 하면 그 사실만 내고 칸을 열지 않는다. 행의 status 로
 * 권한을 넘겨짚지 않는다.
 *
 * 닫는 길은 셋(× · Esc · 바깥 클릭)이고 **고치던 것이 있으면 한 번 묻는다** — 워크스페이스의
 * 이탈 가드가 「다른 회의를 고를 때」를 막는 것과 같은 약속을, 이 모달은 자기 닫기 경로에서 지킨다.
 */
export function MeetingEditModal({
  meetingId,
  onClose,
  onSaved,
  onError,
  onNotice,
}: {
  meetingId: string;
  onClose: () => void;
  /** 저장이 끝났다 — 목록과 (고르고 있었다면) 상세를 다시 읽는 것은 부르는 쪽이 한다. */
  onSaved: (meetingId: string) => void;
  onError: (message: string) => void;
  onNotice: (message: string) => void;
}) {
  const [record, setRecord] = useState<MeetingRecord | null>(null);
  const [failed, setFailed] = useState(false);
  const [head, setHead] = useState<Head | null>(null);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [askClose, setAskClose] = useState(false);
  const { roster } = useRoster(true);

  useEffect(() => {
    let cancelled = false;
    void readMeeting(meetingId)
      .then((next) => {
        if (cancelled) return;
        setRecord(next);
        const meeting = next.meeting;
        setHead({
          // 제목이 비어 있으면 후보를 칸에 채워 연다 — 사람이 그대로 저장하면 그것이 제목이 된다
          title: meeting.title ?? meeting.title_candidate ?? "",
          date: meetingDateInput(meeting.starts_at),
          from: meetingClock(meeting.starts_at),
          to: meetingClock(meeting.ends_at),
          place: meeting.location ?? "",
          people: meeting.attendees.map((one) => ({ ...one })),
        });
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [meetingId]);

  const meeting = record?.meeting ?? null;
  const canEdit = Boolean(meeting?.can_edit_info);

  const unchanged = useMemo(() => {
    if (!head || !meeting) return true;
    return (
      head.title === (meeting.title ?? "") &&
      head.date === meetingDateInput(meeting.starts_at) &&
      head.from === meetingClock(meeting.starts_at) &&
      head.to === meetingClock(meeting.ends_at) &&
      head.place === (meeting.location ?? "") &&
      head.people.map((one) => one.member_id).join() === meeting.attendees.map((one) => one.member_id).join()
    );
  }, [head, meeting]);

  const close = () => (unchanged ? onClose() : setAskClose(true));
  useEscape(close, !askClose);

  async function save() {
    if (!head || !meeting || unchanged || busy) return;
    setBusy(true);
    try {
      await updateMeetingInfo(meeting.meeting_id, {
        title: head.title.trim() || null,
        starts_at: meetingIsoAt(head.date, head.from),
        ends_at: meetingIsoAt(head.date, head.to),
        location: head.place.trim() || null,
        attendee_ids: head.people.map((one) => one.member_id),
      });
      onNotice(meetingScreen.saved);
      onSaved(meeting.meeting_id);
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "저장하지 못했습니다.");
      setBusy(false);
    }
  }

  const pickedIds = new Set((head?.people ?? []).map((one) => one.member_id));

  return (
    <>
      <div className="modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && close()}>
        <section aria-label={meetingScreen.editInfo} aria-modal="true" className="modal meeting-modal-wide" role="dialog">
          <header className="modal-head">
            <h3>{meetingScreen.editInfo}</h3>
            <button aria-label="닫기" className="modal-close" onClick={close} type="button">
              <Icon name="close" size={20} />
            </button>
          </header>

          <div className="modal-body meeting-scroll">
            {failed ? (
              <p className="scax-field__error">{meetingScreen.listError}</p>
            ) : !head || !meeting ? (
              <Skeleton label="회의를 불러오는 중" rows={4} />
            ) : !canEdit ? (
              /* 서버가 「못 고친다」고 했다 — 칸을 열지 않고 그 사실만 낸다 */
              <p className="t-meta">{meetingScreen.cannotEditInfo}</p>
            ) : (
              <div className="meeting-meta-edit">
                <div style={{ display: "flex", flexDirection: "column", gap: 12, minWidth: 0 }}>
                  <div className="scax-field">
                    <label className="scax-field__label" htmlFor="meeting-head-title">
                      {meetingScreen.titleField}
                    </label>
                    <input
                      id="meeting-head-title"
                      onChange={(event) => setHead({ ...head, title: event.target.value })}
                      placeholder={meetingScreen.subjectPlaceholder}
                      type="text"
                      value={head.title}
                    />
                  </div>
                  <div className="scax-field">
                    <label className="scax-field__label" htmlFor="meeting-head-date">
                      {meetingScreen.whenField}
                    </label>
                    {/* 날짜·시각은 공용 부품이다 — 예약 모달과 같은 달력·같은 시각 칩을 쓴다 */}
                    <div className="meeting-when">
                      <DateField
                        formatMonth={formatMonthLong}
                        hideLabel
                        id="meeting-head-date"
                        label={meetingScreen.whenField}
                        labels={datePickerLabel}
                        onChange={(next) => setHead({ ...head, date: next })}
                        today={seoulToday()}
                        value={head.date}
                        weekdayNames={weekdayNames}
                      />
                      <TimeRangeField
                        emptyActionLabel={emptyActionLabel.filter}
                        end={head.to}
                        endLabel={timeFieldLabel.end}
                        label={meetingScreen.whenField}
                        labels={timeFieldLabel}
                        onChange={(next) => setHead({ ...head, from: next.start, to: next.end })}
                        selectLabels={selectLabel}
                        start={head.from}
                        startLabel={timeFieldLabel.start}
                      />
                    </div>
                  </div>
                  {/* 장소는 이름표다 — 회의실 판정·예약은 여기로 오지 않는다 (D14) */}
                  <div className="scax-field">
                    <label className="scax-field__label" htmlFor="meeting-head-place">
                      {meetingScreen.place}
                    </label>
                    <input
                      id="meeting-head-place"
                      onChange={(event) => setHead({ ...head, place: event.target.value })}
                      placeholder={meetingScreen.placeField}
                      type="text"
                      value={head.place}
                    />
                  </div>
                </div>

                <div className="scax-field" style={{ minWidth: 0 }}>
                  <label className="scax-field__label">
                    {meetingScreen.attendees} <span className="count-badge">{head.people.length}</span>
                  </label>
                  <PickedTags
                    items={head.people.map((person) => ({
                      key: person.member_id,
                      name: personName(person.display_name),
                      onRemove: () => setHead({ ...head, people: head.people.filter((one) => one.member_id !== person.member_id) }),
                    }))}
                  />
                  <div style={{ marginTop: 12 }}>
                    <PersonSearch
                      excluded={pickedIds}
                      onPick={(person: RosterPerson) => {
                        setHead({ ...head, people: [...head.people, { member_id: person.member_id, display_name: person.name }] });
                        setQuery("");
                      }}
                      onQueryChange={setQuery}
                      placeholder={meetingScreen.nameSearchPlaceholder}
                      query={query}
                      roster={roster}
                    />
                  </div>
                </div>
              </div>
            )}
          </div>

          <footer className="modal-foot">
            {/* 닫는 자리는 머리의 × 하나다 — 아래에 [취소]를 또 두지 않는다 (I10) */}
            <Button disabled={!canEdit || unchanged || busy} onClick={() => void save()} tone="primary" type="button" variant="solid">
              {meetingScreen.save}
            </Button>
          </footer>
        </section>
      </div>

      {/* 고치던 것이 있는 채로 닫을 때 — 워크스페이스의 이탈 가드와 같은 약속이다 */}
      {askClose && (
        <div className="modal-backdrop" style={{ zIndex: 60 }}>
          <section aria-label={meetingScreen.leaveTitle} aria-modal="true" className="modal" role="alertdialog">
            <header className="modal-head">
              <h3>{meetingScreen.leaveTitle}</h3>
            </header>
            <footer className="modal-foot">
              <Button onClick={() => setAskClose(false)} type="button" variant="text">
                {meetingScreen.keep}
              </Button>
              <Button onClick={onClose} tone="danger" type="button" variant="solid">
                {meetingScreen.leave}
              </Button>
            </footer>
          </section>
        </div>
      )}
    </>
  );
}
