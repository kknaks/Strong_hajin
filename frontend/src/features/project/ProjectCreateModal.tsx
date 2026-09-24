import { useState } from "react";

import { Button } from "../../ds/Button";
import { DateField } from "../../ds/DateField";
import { Modal } from "../../ds/Modal";
import { StatusNote } from "../../ds/StatusNote";
import { datePickerLabel, formatMonthLong, projectScreen, seoulToday, weekdayNames } from "../../lib/labels";

/** `POST /api/projects` 가 받는 다섯 중 **화면이 내는 넷**. 빈 칸은 보내지 않는다. */
export type ProjectCreateInput = {
  name: string;
  description?: string | null;
  starts_on?: string | null;
  ends_on?: string | null;
};

/**
 * 프로젝트 만들기 — **생성 전용 모달** (D-30 · SPEC-005 §2.10).
 *
 * 관리 모달을 열지 않는다: 만들러 온 사람에게 **참여자 관리가 먼저 보이지 않는다.**
 * ⚠ 루프2 동안 **생성 경로가 둘**이다 — 관리 모달 안의 「새 프로젝트」 폼은 **루프3 이 뗀다**(D-30).
 * 그 중복은 감수하는 것이지 조용한 것이 아니다.
 *
 * **칸은 넷이다 — `external_key` 칸이 없다.** 표면이 그 값을 **못 받아서가 아니라**(선택으로 받는다)
 * 그것이 데이터셋 import 의 외부 식별자이고 **사람이 손으로 채울 값이 아니기 때문**이다. 손으로 채우면
 * 중복에서 DB unique 제약에 걸린다 (미결 **OQ-607**). **FE api 래퍼도 넷만 보낸다 — 넓히지 않는다.**
 *
 * **거절 네 갈래를 이 모달이 말한다** (§4 에러 — 새 코드 0건):
 * ① 자격 없음 ② 이름 없음/공백만 ③ 기간 역전 ④ `external_key` 중복.
 * ②는 여기서 먼저 막고(보낼 수 없다), 나머지는 **서버 문구를 그대로** 이 자리에 낸다 —
 * 모달을 닫지 않으므로 사람이 **쓰던 값을 고쳐 다시 보낼 수 있다.**
 * (④는 화면이 그 칸을 안 내므로 이 경로에서는 나지 않는다. 그래도 문구는 그대로 흘려보낸다 —
 * 서버가 거절하면 **무엇이든** 이 자리가 말한다.)
 */
export function ProjectCreateModal({
  busy,
  onClose,
  onCreate,
}: {
  busy: boolean;
  onClose: () => void;
  /** 만든다. **받아들여지지 않으면 그 사유 문구**를, 성공하면 `null` 을 돌려준다. */
  onCreate: (input: ProjectCreateInput) => Promise<string | null>;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [startsOn, setStartsOn] = useState("");
  const [endsOn, setEndsOn] = useState("");
  const [rejected, setRejected] = useState<string | null>(null);
  /** 보내는 중 — 두 번 눌러 두 개를 만들지 않는다. */
  const [sending, setSending] = useState(false);

  /* 서버가 이름의 공백을 정규화한 뒤 빈 문자열이면 거절한다 — **공백만으로는 만들 수 없다**(L-20).
     그 규칙을 화면에서 다시 «계산» 하지 않고, 보낼 수 없다는 것만 여기서 말한다. */
  const naming = name.trim();
  const locked = busy || sending;

  async function submit() {
    if (!naming || locked) return;
    setSending(true);
    setRejected(null);
    /* 빈 칸은 키째 보내지 않는다 — 「비움」과 「안 건드림」을 같은 말로 보내지 않는다. */
    const message = await onCreate({
      name: naming,
      ...(description.trim() ? { description: description.trim() } : {}),
      ...(startsOn ? { starts_on: startsOn } : {}),
      ...(endsOn ? { ends_on: endsOn } : {}),
    });
    setSending(false);
    if (message) setRejected(message);
  }

  return (
    <Modal
      closeLabel={projectScreen.createClose}
      footer={
        <>
          <Button disabled={locked || !naming} onClick={() => void submit()} size="sm" tone="primary" type="button" variant="solid">
            {projectScreen.createSubmit}
          </Button>
          <Button disabled={locked} onClick={onClose} size="sm" type="button" variant="text">
            {projectScreen.createCancel}
          </Button>
        </>
      }
      label={projectScreen.createTitle}
      onClose={onClose}
      size="md"
      title={projectScreen.createTitle}
    >
      <div className="scax-pj-create">
        {/* 거절은 **쓰던 자리 옆에서** 말한다 — 모달이 닫히고 화면 아래에서 말하면 무엇을 고칠지 모른다. */}
        {rejected && <StatusNote tone="danger">{rejected}</StatusNote>}

        <div className="scax-field">
          <label className="scax-field__label" htmlFor="project-create-name">
            {projectScreen.createName}
          </label>
          <div className="scax-textfield">
            <input
              className="scax-textfield__input"
              disabled={locked}
              id="project-create-name"
              maxLength={300}
              onChange={(event) => setName(event.target.value)}
              placeholder={projectScreen.createNamePlaceholder}
              value={name}
            />
          </div>
        </div>

        <div className="scax-field">
          <label className="scax-field__label" htmlFor="project-create-description">
            {projectScreen.createDescription}
          </label>
          <textarea
            className="scax-pj-create__text"
            disabled={locked}
            id="project-create-description"
            onChange={(event) => setDescription(event.target.value)}
            placeholder={projectScreen.createDescriptionPlaceholder}
            rows={3}
            value={description}
          />
        </div>

        {/* 기간 역전은 **서버가 정본**이다 — 화면이 그 판정을 다시 하지 않고 거절 문구를 받아 낸다. */}
        <div className="scax-pj-create__dates">
          <div className="scax-field">
            <DateField
              disabled={locked}
              displaySeparator="."
              formatMonth={formatMonthLong}
              id="project-create-starts-on"
              label={projectScreen.createStartsOn}
              labels={datePickerLabel}
              onChange={setStartsOn}
              today={seoulToday()}
              value={startsOn}
              weekdayNames={weekdayNames}
            />
          </div>
          <div className="scax-field">
            <DateField
              disabled={locked}
              displaySeparator="."
              formatMonth={formatMonthLong}
              id="project-create-ends-on"
              label={projectScreen.createEndsOn}
              labels={datePickerLabel}
              onChange={setEndsOn}
              today={seoulToday()}
              value={endsOn}
              weekdayNames={weekdayNames}
            />
          </div>
        </div>
      </div>
    </Modal>
  );
}
